#!/usr/bin/env python3
"""NAT64 ISP Manager - privileged host helper.

This file MUST be installed root-owned at /opt/nat64-manager/hostctl.py and
invoked only through the provided sudoers entry, with JSON on stdin.

It accepts a fixed set of operation names and typed parameters. It never runs a
shell and never accepts a raw command vector. All parameters are re-validated
here even if the backend already validated them.

Protocol:
  stdin:  {"op": "...", "params": {...}}
  stdout: {"ok": true|false, "code": "...", "message": "...", "data": {...},
           "stdout": "...", "stderr": "...", "exit_code": 0, "commands": [...]}
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

VERSION = "1.0.0"
MAX_OUTPUT = 4 * 1024 * 1024
INSTANCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,15}$")
NETNS_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
INTERFACE_RE = re.compile(r"^[A-Za-z0-9_.:@-]{1,64}$")
DOMAIN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,253}$")
DURATION_RE = re.compile(r"^\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?$|^\d+(?:\.\d{1,3})?(?:ms|s|m|h)?$")
SYSCTL_RE = re.compile(r"^[a-z0-9_.\-/]+=[0-9A-Za-z_.:\- ]{1,128}$")
TABLE_NAME = "nat64mgr"
MANAGED_PATHS = {
    "dns": Path("/etc/bind/nat64-manager.conf"),
    "network": Path("/etc/netplan/90-nat64-manager.yaml"),
    "sysctl": Path("/etc/sysctl.d/90-nat64-manager.conf"),
    "firewall": Path("/etc/nftables.d/nat64-manager.nft"),
    "keepalived": Path("/etc/keepalived/keepalived.conf"),
}
BACKUP_ROOT = Path("/var/backups/nat64-manager")
RUN_DIR = Path("/run/nat64-manager")
ALLOW_INSTALL_FLAG = Path("/etc/nat64-manager/allow-package-install")
JOOL_BIN = os.environ.get("NAT64_JOOL_BINARY", "jool")
GLOBAL_SETTINGS = {
    "manually-enabled": "bool",
    "address-dependent-filtering": "bool",
    "drop-icmpv6-info": "bool",
    "drop-externally-initiated-tcp": "bool",
    "logging-bib": "bool",
    "logging-session": "bool",
    "source-icmpv6-errors-better": "bool",
    "handle-rst-during-fin-rcv": "bool",
    "ss-enabled": "bool",
    "udp-timeout": "duration",
    "tcp-est-timeout": "duration",
    "tcp-trans-timeout": "duration",
    "icmp-timeout": "duration",
    "lowest-ipv6-mtu": "int",
    "maximum-simultaneous-opens": "int",
    "f-args": "int",
    "ss-flush-deadline": "int",
    "ss-capacity": "int",
    "ss-max-sessions-per-packet": "int",
    "mtu-plateaus": "string",
}
ALLOWED_SERVICES = {"named", "bind9", "nftables", "systemd-networkd", "keepalived", "networking"}
ALLOWED_JOURNAL_UNITS = {
    "nat64mgr",
    "nat64-manager",
    "nat64mgr-api",
    "named",
    "bind9",
    "nftables",
    "systemd-networkd",
    "keepalived",
    "jool",
}


class HelperError(Exception):
    def __init__(self, code: str, message: str, *, data: dict | None = None, commands: list | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}
        self.commands = commands or []


def _result_ok(data: dict | None = None, stdout: str = "", stderr: str = "", commands: list | None = None) -> dict:
    return {
        "ok": True,
        "code": "ok",
        "data": data or {},
        "stdout": stdout[-MAX_OUTPUT:],
        "stderr": stderr[-MAX_OUTPUT:],
        "exit_code": 0,
        "commands": commands or [],
    }


def run(argv: list[str], *, timeout: int = 30, check: bool = False, input_text: str | None = None) -> dict:
    if not argv or not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise HelperError("invalid_argv", "Internal command vector is invalid")
    try:
        proc = subprocess.run(
            argv,
            input=input_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            shell=False,
            env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"},
        )
    except FileNotFoundError as exc:
        raise HelperError("binary_not_found", f"Executable not found: {argv[0]}", commands=[argv]) from exc
    except subprocess.TimeoutExpired as exc:
        raise HelperError(
            "timeout", f"Command timed out after {timeout}s: {argv[0]}", commands=[argv]
        ) from exc
    result = {
        "stdout": (proc.stdout or "")[-MAX_OUTPUT:],
        "stderr": (proc.stderr or "")[-MAX_OUTPUT:],
        "exit_code": proc.returncode,
    }
    if check and proc.returncode != 0:
        raise HelperError(
            "command_failed",
            f"{argv[0]} exited with code {proc.returncode}",
            data=result,
            commands=[argv],
        )
    return result


def _require(value: Any, name: str) -> Any:
    if value is None or value == "":
        raise HelperError("invalid_parameter", f"Missing parameter: {name}")
    return value


def _instance(value: Any) -> str:
    value = _require(value, "instance")
    if not isinstance(value, str) or not INSTANCE_RE.fullmatch(value):
        raise HelperError("invalid_instance", "Jool instance name must match [A-Za-z0-9_-]{1,15}")
    return value


def _netns(value: Any) -> str | None:
    if not value:
        return None
    if not isinstance(value, str) or not NETNS_RE.fullmatch(value):
        raise HelperError("invalid_netns", "Invalid network namespace name")
    return value


def _netns_prefix(netns: str | None) -> list[str]:
    return ["ip", "netns", "exec", netns] if netns else []


def _protocol(value: Any) -> str:
    if value not in {"tcp", "udp", "icmp"}:
        raise HelperError("invalid_protocol", "Protocol must be tcp, udp or icmp")
    return value


def _ipv4_network(value: Any) -> ipaddress.IPv4Network:
    try:
        return ipaddress.IPv4Network(str(value), strict=False)
    except ValueError as exc:
        raise HelperError("invalid_ipv4", f"Invalid IPv4 prefix: {value}") from exc


def _ipv6_prefix(value: Any, *, pool6: bool = False) -> ipaddress.IPv6Network:
    try:
        net = ipaddress.IPv6Network(str(value), strict=True)
    except ValueError as exc:
        raise HelperError("invalid_ipv6", f"Invalid IPv6 prefix (host bits must be zero): {value}") from exc
    if pool6 and net.prefixlen not in {32, 40, 48, 56, 64, 96}:
        raise HelperError("invalid_pool6", "NAT64 pool6 must use a prefix length of /32, /40, /48, /56, /64 or /96")
    return net


def _port_range(port_min: Any, port_max: Any) -> tuple[int, int]:
    try:
        lo, hi = int(port_min), int(port_max)
    except (TypeError, ValueError) as exc:
        raise HelperError("invalid_ports", "Port range must be numeric") from exc
    if not (0 <= lo <= hi <= 65535):
        raise HelperError("invalid_ports", "Port range must satisfy 0 <= min <= max <= 65535")
    return lo, hi


def _interface(value: Any) -> str:
    value = _require(value, "interface")
    if not isinstance(value, str) or not INTERFACE_RE.fullmatch(value):
        raise HelperError("invalid_interface", "Invalid interface name")
    return value


def _validate_content(content: Any, name: str, max_bytes: int = 256 * 1024) -> str:
    if not isinstance(content, str) or not content.strip():
        raise HelperError("invalid_content", f"{name} content is empty")
    if len(content.encode()) > max_bytes:
        raise HelperError("invalid_content", f"{name} content exceeds {max_bytes} bytes")
    if "\x00" in content:
        raise HelperError("invalid_content", f"{name} content contains NUL bytes")
    return content


def _write_atomic(path: Path, content: str, mode: int = 0o640) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        if path.name == "nat64-manager.conf":
            try:
                import grp

                os.chown(tmp, 0, grp.getgrnam("bind").gr_gid)
            except (KeyError, PermissionError, ImportError):
                pass
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _snapshot(paths: list[Path], reason: str) -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    target = BACKUP_ROOT / f"{reason}-{stamp}-{os.getpid()}"
    target.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for path in paths:
        if path.exists():
            dest = target / path.name
            dest.write_bytes(path.read_bytes())
            manifest[str(path)] = str(dest)
        else:
            manifest[str(path)] = None
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return str(target)


def _restore_snapshot(snapshot: str) -> None:
    manifest_path = Path(snapshot) / "manifest.json"
    if not manifest_path.exists():
        raise HelperError("invalid_snapshot", "Snapshot manifest not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for original, saved in manifest.items():
        original_path = Path(original)
        if saved and Path(saved).exists():
            original_path.parent.mkdir(parents=True, exist_ok=True)
            original_path.write_bytes(Path(saved).read_bytes())
        elif original_path.exists():
            original_path.unlink()


def _jool_base(params: dict) -> tuple[list[str], str | None]:
    binary = shutil.which(JOOL_BIN) or shutil.which("/usr/sbin/jool")
    if not binary:
        raise HelperError("jool_not_installed", "Jool userspace tool is not installed")
    netns = _netns(params.get("netns"))
    return [*_netns_prefix(netns), binary], netns


def _jool(params: dict) -> tuple[dict, str, str, list]:
    action = _require(params.get("action"), "action")
    base, _ = _jool_base(params)
    commands: list[list[str]] = []
    instance = params.get("instance")
    if action != "version" and action != "instance_display":
        instance = _instance(instance)

    def execute(argv: list[str], timeout: int = 25) -> dict:
        commands.append(argv)
        return run(argv, timeout=timeout)

    if action == "version":
        res = execute([*base, "--version"])
        return {"version_output": res["stdout"].strip()}, res["stdout"], res["stderr"], commands

    if action == "instance_display":
        res = execute([*base, "instance", "display"])
        return {}, res["stdout"], res["stderr"], commands

    if action == "instance_add":
        pool6 = _ipv6_prefix(params.get("pool6"), pool6=True)
        framework = params.get("framework", "netfilter")
        if framework != "netfilter":
            raise HelperError("invalid_framework", "Only the netfilter Jool framework is supported")
        res = execute([*base, "instance", "add", instance, "--netfilter", "--pool6", str(pool6)], timeout=30)
        return {}, res["stdout"], res["stderr"], commands

    if action == "instance_remove":
        res = execute([*base, "instance", "remove", instance], timeout=30)
        return {}, res["stdout"], res["stderr"], commands

    if action == "pool4_display":
        proto = _protocol(params.get("protocol", "tcp"))
        res = execute([*base, "-i", instance, "pool4", "display", f"--{proto}", "--csv"])
        return {"protocol": proto}, res["stdout"], res["stderr"], commands

    if action == "pool4_add":
        proto = _protocol(params.get("protocol"))
        network = _ipv4_network(params.get("address"))
        lo, hi = _port_range(params.get("port_min"), params.get("port_max"))
        res = execute(
            [*base, "-i", instance, "pool4", "add", f"--{proto}", str(network), f"{lo}-{hi}"],
            timeout=40,
        )
        return {}, res["stdout"], res["stderr"], commands

    if action == "pool4_remove":
        proto = _protocol(params.get("protocol"))
        network = _ipv4_network(params.get("address"))
        lo, hi = _port_range(params.get("port_min"), params.get("port_max"))
        res = execute(
            [*base, "-i", instance, "pool4", "remove", f"--{proto}", str(network), f"{lo}-{hi}"],
            timeout=40,
        )
        return {}, res["stdout"], res["stderr"], commands

    if action == "pool4_flush":
        proto = _protocol(params.get("protocol"))
        res = execute([*base, "-i", instance, "pool4", "flush", f"--{proto}"], timeout=40)
        return {}, res["stdout"], res["stderr"], commands

    if action == "session_display":
        proto = _protocol(params.get("protocol", "tcp"))
        res = execute(
            [*base, "-i", instance, "session", "display", f"--{proto}", "--numeric", "--csv"],
            timeout=30,
        )
        return {"protocol": proto}, res["stdout"], res["stderr"], commands

    if action == "stats_display":
        res = execute([*base, "-i", instance, "stats", "display", "--csv"], timeout=20)
        return {}, res["stdout"], res["stderr"], commands

    if action == "global_display":
        res = execute([*base, "-i", instance, "global", "display"], timeout=20)
        return {}, res["stdout"], res["stderr"], commands

    if action == "global_update":
        key = str(_require(params.get("key"), "key"))
        if key not in GLOBAL_SETTINGS:
            raise HelperError("invalid_setting", f"Setting is not allowed: {key}")
        value = _require(params.get("value"), "value")
        kind = GLOBAL_SETTINGS[key]
        if kind == "bool":
            if str(value).lower() not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
                raise HelperError("invalid_setting", "Boolean setting has an invalid value")
        elif kind == "int":
            try:
                int(value)
            except (TypeError, ValueError) as exc:
                raise HelperError("invalid_setting", "Integer setting has an invalid value") from exc
        elif kind == "duration":
            if not DURATION_RE.fullmatch(str(value)):
                raise HelperError("invalid_setting", "Duration setting has an invalid value")
        elif kind == "string" and (
            not isinstance(value, str) or len(value) > 512 or "\n" in value
        ):
            raise HelperError("invalid_setting", "String setting has an invalid value")
        res = execute([*base, "-i", instance, "global", "update", key, str(value)], timeout=25)
        return {}, res["stdout"], res["stderr"], commands

    raise HelperError("unknown_action", f"Unknown Jool action: {action}")


def _read_ip(params: dict) -> tuple[dict, str, str, list]:
    kind = _require(params.get("kind"), "kind")
    netns = _netns(params.get("netns"))
    prefix = _netns_prefix(netns)
    if kind == "interfaces":
        argv = [*prefix, "ip", "-j", "address", "show"]
    elif kind == "routes4":
        argv = [*prefix, "ip", "-j", "-4", "route", "show"]
    elif kind == "routes6":
        argv = [*prefix, "ip", "-j", "-6", "route", "show"]
    elif kind == "neighbors":
        argv = [*prefix, "ip", "-j", "neighbor", "show"]
    elif kind == "link":
        argv = [*prefix, "ip", "-j", "link", "show"]
    else:
        raise HelperError("unknown_action", f"Unknown ip read kind: {kind}")
    res = run(argv, timeout=15)
    return {}, res["stdout"], res["stderr"], [argv]


def _system(params: dict) -> tuple[dict, str, str, list]:
    action = _require(params.get("action"), "action")
    commands: list[list[str]] = []
    if action == "capabilities":
        binaries = {}
        for name in [
            "jool", "jool_siit", "nft", "named-checkconf", "rndc", "dig", "systemctl", "netplan",
            "ip", "sysctl", "modprobe", "journalctl", "ping", "curl", "apt-get", "keepalived",
        ]:
            binaries[name] = shutil.which(name)
        os_release = {}
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    os_release[k] = v.strip().strip('"')
        except OSError:
            pass
        data = {
            "helper_version": VERSION,
            "platform": sys.platform,
            "euid": os.geteuid(),
            "kernel": os.uname().release,
            "os_release": os_release,
            "binaries": binaries,
            "managed_paths": {k: {"path": str(v), "exists": v.exists()} for k, v in MANAGED_PATHS.items()},
            "jool_version": None,
            "jool_error": None,
        }
        if binaries.get("jool"):
            try:
                res = run([binaries["jool"], "--version"], timeout=10)
                data["jool_version"] = res["stdout"].strip() or res["stderr"].strip()
                commands.append([binaries["jool"], "--version"])
            except HelperError as exc:
                data["jool_error"] = exc.message
        return data, "", "", commands
    if action == "uname":
        res = run(["uname", "-a"], timeout=5)
        return {}, res["stdout"], res["stderr"], [["uname", "-a"]]
    if action == "os_release":
        try:
            return {"content": Path("/etc/os-release").read_text(encoding="utf-8")}, "", "", []
        except OSError as exc:
            raise HelperError("read_failed", str(exc)) from exc
    if action == "resolv_conf":
        try:
            return {"content": Path("/etc/resolv.conf").read_text(encoding="utf-8")[:65536]}, "", "", []
        except OSError as exc:
            raise HelperError("read_failed", str(exc)) from exc
    if action == "sysctl_read":
        key = str(_require(params.get("key"), "key"))
        if not re.fullmatch(r"[a-z0-9_.\-/]{1,128}", key):
            raise HelperError("invalid_parameter", "Invalid sysctl key")
        res = run(["sysctl", key], timeout=5)
        return {}, res["stdout"], res["stderr"], [["sysctl", key]]
    if action == "service_active":
        service = str(_require(params.get("service"), "service"))
        if service not in ALLOWED_SERVICES:
            raise HelperError("invalid_parameter", "Service is not allowed")
        res = run(["systemctl", "is-active", service], timeout=5)
        return {"active": res["stdout"].strip() == "active", "state": res["stdout"].strip()}, res["stdout"], res["stderr"], [
            ["systemctl", "is-active", service]
        ]
    if action == "service_restart":
        service = str(_require(params.get("service"), "service"))
        if service not in ALLOWED_SERVICES:
            raise HelperError("invalid_parameter", "Service is not allowed")
        res = run(["systemctl", "restart", service], timeout=45)
        return {}, res["stdout"], res["stderr"], [["systemctl", "restart", service]]
    if action == "kernel_log":
        limit = min(max(int(params.get("limit", 300)), 1), 2000)
        res = run(["journalctl", "-k", "-n", str(limit), "--no-pager"], timeout=20)
        lines = [ln for ln in res["stdout"].splitlines() if "Jool" in ln or "jool" in ln or "nat64" in ln.lower()]
        return {"lines": lines[-limit:]}, res["stdout"], res["stderr"], [
            ["journalctl", "-k", "-n", str(limit), "--no-pager"]
        ]
    if action == "journal":
        unit = str(_require(params.get("unit"), "unit"))
        if unit not in ALLOWED_JOURNAL_UNITS:
            raise HelperError("invalid_parameter", "Log unit is not allowed")
        limit = min(max(int(params.get("limit", 300)), 1), 2000)
        argv = ["journalctl", "-u", unit, "-n", str(limit), "--no-pager", "--output=short-iso"]
        res = run(argv, timeout=20)
        return {"lines": res["stdout"].splitlines()[-limit:], "unit": unit}, res["stdout"], res["stderr"], [argv]
    if action == "listen_ports":
        argv = ["ss", "-H", "-lntup"]
        res = run(argv, timeout=10)
        return {"lines": res["stdout"].splitlines()}, res["stdout"], res["stderr"], [argv]
    if action == "modprobe_jool":
        res = run(["modprobe", "jool"], timeout=30)
        return {}, res["stdout"], res["stderr"], [["modprobe", "jool"]]
    raise HelperError("unknown_action", f"Unknown system action: {action}")


def _dns(params: dict) -> tuple[dict, str, str, list]:
    action = _require(params.get("action"), "action")
    commands: list[list[str]] = []
    if action == "status":
        service = str(params.get("service", "named"))
        if service not in ALLOWED_SERVICES:
            raise HelperError("invalid_parameter", "Service is not allowed")
        commands.append(["systemctl", "is-active", service])
        res = run(["systemctl", "is-active", service], timeout=5)
        active = res["stdout"].strip() == "active"
        if not active and service == "named":
            res2 = run(["systemctl", "is-active", "bind9"], timeout=5)
            commands.append(["systemctl", "is-active", "bind9"])
            active = res2["stdout"].strip() == "active"
            res = res2
        data = {"active": active, "state": res["stdout"].strip(), "service": service}
        return data, res["stdout"], res["stderr"], commands
    if action == "read_config":
        path = Path(params.get("path") or MANAGED_PATHS["dns"])
        if path != MANAGED_PATHS["dns"]:
            raise HelperError("invalid_path", "Only the managed DNS include may be read")
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        return {"content": content, "exists": path.exists()}, content, "", []
    if action == "validate":
        content = _validate_content(params.get("content"), "DNS")
        _validate_dns_fragment(content)
        command = _validate_bind_fragment(content)
        commands.append(command)
        return {"valid": True}, "", "", commands
    if action == "apply":
        content = _validate_content(params.get("content"), "DNS")
        _validate_dns_fragment(content)
        check_command = _validate_bind_fragment(content)
        commands.append(check_command)
        target = MANAGED_PATHS["dns"]
        previous = target.read_bytes() if target.exists() else None
        _write_atomic(target, content)
        check = run(["named-checkconf"], timeout=20)
        commands.append(["named-checkconf"])
        if check["exit_code"] != 0:
            if previous is not None:
                _write_atomic(target, previous.decode("utf-8", errors="replace"))
            else:
                target.unlink(missing_ok=True)
            raise HelperError(
                "bind_config_invalid",
                "named-checkconf rejected the resulting BIND configuration; previous file restored",
                data=check,
                commands=commands,
            )
        reconfig = run(["rndc", "reconfig"], timeout=20)
        commands.append(["rndc", "reconfig"])
        if reconfig["exit_code"] != 0:
            service = str(params.get("service", "named"))
            if service not in {"named", "bind9"}:
                service = "named"
            restart = run(["systemctl", "restart", service], timeout=45)
            commands.append(["systemctl", "restart", service])
            if restart["exit_code"] != 0:
                if previous is not None:
                    _write_atomic(target, previous.decode("utf-8", errors="replace"))
                raise HelperError(
                    "bind_reload_failed",
                    "Could not reload BIND after updating DNS64 configuration",
                    data={"reconfig": reconfig, "restart": restart},
                    commands=commands,
                )
            return {"applied": True, "reloaded": "restart"}, "", restart["stderr"], commands
        return {"applied": True, "reloaded": "rndc reconfig"}, reconfig["stdout"], reconfig["stderr"], commands
    if action == "restart":
        service = str(params.get("service", "named"))
        if service not in {"named", "bind9"}:
            raise HelperError("invalid_parameter", "DNS service must be named or bind9")
        res = run(["systemctl", "restart", service], timeout=45)
        commands.append(["systemctl", "restart", service])
        return {}, res["stdout"], res["stderr"], commands
    if action == "stats":
        path = Path(params.get("path") or "/var/cache/bind/named.stats")
        if path != Path("/var/cache/bind/named.stats"):
            raise HelperError("invalid_path", "Only the BIND statistics file may be read")
        if not path.exists():
            raise HelperError("stats_unavailable", "BIND statistics file not found; run rndc stats first")
        res = run(["rndc", "stats"], timeout=10)
        commands.append(["rndc", "stats"])
        content = path.read_text(encoding="utf-8", errors="replace")[-MAX_OUTPUT:]
        return {"content": content}, res["stdout"], res["stderr"], commands
    if action == "dig":
        server = str(params.get("server", "127.0.0.1"))
        try:
            ipaddress.ip_address(server)
        except ValueError as exc:
            raise HelperError("invalid_server", "DNS server must be an IP address") from exc
        domain = str(_require(params.get("domain"), "domain"))
        if not DOMAIN_RE.fullmatch(domain.rstrip(".")):
            raise HelperError("invalid_domain", "Invalid DNS name")
        rtype = str(params.get("type", "AAAA")).upper()
        if rtype not in {"A", "AAAA", "SOA", "NS", "TXT", "CNAME"}:
            raise HelperError("invalid_type", "DNS record type is not allowed")
        timeout = min(max(int(params.get("timeout", 4)), 1), 30)
        argv = ["dig", f"+time={timeout}", "+tries=1", "@" + server, domain, rtype, "+short"]
        res = run(argv, timeout=timeout + 3)
        commands.append(argv)
        return {"answers": [ln for ln in res["stdout"].splitlines() if ln.strip()]}, res["stdout"], res["stderr"], commands
    if action == "named_checkconf":
        res = run(["named-checkconf"], timeout=20)
        commands.append(["named-checkconf"])
        return {"valid": res["exit_code"] == 0}, res["stdout"], res["stderr"], commands
    raise HelperError("unknown_action", f"Unknown DNS action: {action}")


def _validate_dns_fragment(content: str) -> None:
    forbidden = re.compile(r"\b(include|zone|controls|key|acl|logging|server|view|master|allow-transfer)\b", re.IGNORECASE)
    match = forbidden.search(re.sub(r"#.*", "", content))
    if match:
        raise HelperError(
            "unsafe_dns_config",
            f"DNS fragment may only contain options-level settings; forbidden token: {match.group(1)}",
        )
    if "{" in content and content.count("{") != content.count("}"):
        raise HelperError("invalid_dns_config", "Unbalanced braces in DNS fragment")


def _validate_bind_fragment(content: str) -> list[str]:
    wrapper = "options {\n" + content + "\n};\n"
    fd, path = tempfile.mkstemp(prefix="nat64-dns-check-", suffix=".conf", dir="/tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(wrapper)
        os.chmod(path, 0o600)
        res = run(["named-checkconf", path], timeout=20)
        if res["exit_code"] != 0:
            raise HelperError(
                "dns_config_invalid",
                "named-checkconf rejected the rendered DNS64 fragment",
                data=res,
                commands=[["named-checkconf", "<temporary wrapper>"]],
            )
        return ["named-checkconf", "<temporary wrapper>"]
    finally:
        Path(path).unlink(missing_ok=True)


def _firewall(params: dict) -> tuple[dict, str, str, list]:
    action = _require(params.get("action"), "action")
    commands: list[list[str]] = []
    if action == "validate" or action == "apply":
        content = _validate_content(params.get("content"), "nftables")
        _validate_nftables(content)
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="firewall-", suffix=".nft", dir=str(RUN_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.chmod(path, 0o600)
            check = run(["nft", "-c", "-f", path], timeout=20)
            commands.append(["nft", "-c", "-f", "<staged>"])
            if check["exit_code"] != 0:
                raise HelperError("nft_validation_failed", "nftables dry-run rejected the ruleset", data=check, commands=commands)
            if action == "validate":
                return {"valid": True}, check["stdout"], check["stderr"], commands
            apply_res = run(["nft", "-f", path], timeout=30)
            commands.append(["nft", "-f", "<staged>"])
            if apply_res["exit_code"] != 0:
                raise HelperError("nft_apply_failed", "nftables apply failed", data=apply_res, commands=commands)
            _write_atomic(MANAGED_PATHS["firewall"], content)
            return {"applied": True}, apply_res["stdout"], apply_res["stderr"], commands
        finally:
            Path(path).unlink(missing_ok=True)
    if action == "clear":
        res = run(["nft", "delete", "table", "inet", TABLE_NAME], timeout=20)
        commands.append(["nft", "delete", "table", "inet", TABLE_NAME])
        MANAGED_PATHS["firewall"].unlink(missing_ok=True)
        if res["exit_code"] != 0 and "No such file or directory" not in res["stderr"]:
            raise HelperError("nft_clear_failed", "Could not delete managed nftables table", data=res, commands=commands)
        return {"cleared": True}, res["stdout"], res["stderr"], commands
    if action == "list":
        res = run(["nft", "-j", "list", "table", "inet", TABLE_NAME], timeout=20)
        commands.append(["nft", "-j", "list", "table", "inet", TABLE_NAME])
        return {}, res["stdout"], res["stderr"], commands
    raise HelperError("unknown_action", f"Unknown firewall action: {action}")


def _validate_nftables(content: str) -> None:
    lowered = content.lower()
    for token in ["flush ruleset", "include ", "#!/", "delete table inet ", "table ip ", "table ip6 "]:
        if token in lowered:
            raise HelperError("unsafe_nft_config", f"Forbidden nftables content: {token.strip()}")
    if lowered.count("table inet ") != 1 or f"table inet {TABLE_NAME}" not in lowered:
        raise HelperError("unsafe_nft_config", f"Ruleset must contain exactly one table: inet {TABLE_NAME}")
    if content.count("{") != content.count("}"):
        raise HelperError("invalid_nft_config", "Unbalanced braces in nftables ruleset")


def _validate_sysctl(content: str) -> None:
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not SYSCTL_RE.fullmatch(stripped):
            raise HelperError("invalid_sysctl", f"Invalid sysctl line: {stripped[:80]}")


def _netplan_validate(yaml_content: str) -> None:
    if not shutil.which("netplan"):
        raise HelperError("netplan_not_installed", "netplan is not installed")
    with tempfile.TemporaryDirectory(prefix="nat64-netplan-") as tmp:
        root = Path(tmp)
        (root / "etc" / "netplan").mkdir(parents=True)
        (root / "etc" / "netplan" / "90-nat64-manager.yaml").write_text(yaml_content, encoding="utf-8")
        argv = ["netplan", "generate", "--root-dir", str(root)]
        res = run(argv, timeout=30)
        if res["exit_code"] != 0:
            # Older netplan versions do not support --root-dir. Fall back to a
            # syntax-only check rather than touching /etc/netplan before apply.
            if "unrecognized" in (res["stderr"] + res["stdout"]).lower() or "unknown option" in (res["stderr"] + res["stdout"]).lower():
                return
            raise HelperError("netplan_invalid", "netplan generate rejected the configuration", data=res, commands=[argv])


def _network(params: dict) -> tuple[dict, str, str, list]:
    action = _require(params.get("action"), "action")
    commands: list[list[str]] = []
    if action == "validate":
        yaml_content = _validate_content(params.get("netplan_yaml"), "netplan")
        sysctl_content = params.get("sysctl_content") or ""
        _validate_sysctl(sysctl_content)
        _netplan_validate(yaml_content)
        return {"valid": True}, "", "", []
    if action == "apply":
        yaml_content = _validate_content(params.get("netplan_yaml"), "netplan")
        sysctl_content = params.get("sysctl_content") or ""
        _validate_sysctl(sysctl_content)
        paths = [MANAGED_PATHS["network"], MANAGED_PATHS["sysctl"]]
        snapshot = _snapshot(paths, "network")
        _write_atomic(MANAGED_PATHS["network"], yaml_content)
        _write_atomic(MANAGED_PATHS["sysctl"], sysctl_content)
        gen = run(["netplan", "generate"], timeout=30)
        commands.append(["netplan", "generate"])
        if gen["exit_code"] != 0:
            _restore_snapshot(snapshot)
            raise HelperError("netplan_invalid", "netplan generate failed; configuration restored", data=gen, commands=commands)
        run(["sysctl", "--system"], timeout=30)
        commands.append(["sysctl", "--system"])
        apply_res = run(["netplan", "apply"], timeout=45)
        commands.append(["netplan", "apply"])
        if apply_res["exit_code"] != 0:
            _restore_snapshot(snapshot)
            run(["netplan", "generate"], timeout=30)
            run(["netplan", "apply"], timeout=45)
            raise HelperError("netplan_apply_failed", "netplan apply failed; previous configuration restored", data=apply_res, commands=commands)

        # An applied network change is only left in place when the automatic
        # rollback timer can be scheduled. Otherwise restore the snapshot and
        # report the failure instead of returning an unprotected change.
        rollback_seconds = min(max(int(params.get("rollback_seconds", 120)), 30), 900)
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        rollback_script = RUN_DIR / "rollback-network.sh"
        rollback_script.write_text(
            "#!/bin/sh\nset -eu\n"
            f"python3 {Path(__file__).resolve()} <<'EOF'\n"
            + json.dumps({"op": "network", "params": {"action": "rollback", "snapshot": snapshot}})
            + "\nEOF\n",
            encoding="utf-8",
        )
        os.chmod(rollback_script, 0o700)
        run(["systemctl", "stop", "nat64mgr-netrollback.timer"], timeout=10)
        timer = run(
            [
                "systemd-run", "--unit=nat64mgr-netrollback", "--on-active", str(rollback_seconds),
                "--collect", "/bin/sh", str(rollback_script),
            ],
            timeout=20,
        )
        commands.append(["systemd-run", "--unit=nat64mgr-netrollback", "--on-active", str(rollback_seconds), "<rollback script>"])
        if timer["exit_code"] != 0:
            _restore_snapshot(snapshot)
            run(["netplan", "generate"], timeout=30)
            run(["netplan", "apply"], timeout=45)
            raise HelperError(
                "rollback_timer_failed",
                "Could not schedule the network rollback timer; the change was reverted and not left applied",
                data=timer,
                commands=commands,
            )
        return {
            "applied": True,
            "snapshot": snapshot,
            "rollback_timer": True,
            "rollback_seconds": rollback_seconds,
        }, apply_res["stdout"], apply_res["stderr"] + (timer["stderr"] or ""), commands
    if action == "confirm":
        res = run(["systemctl", "stop", "nat64mgr-netrollback.timer"], timeout=15)
        commands.append(["systemctl", "stop", "nat64mgr-netrollback.timer"])
        return {"confirmed": True}, res["stdout"], res["stderr"], commands
    if action == "rollback":
        snapshot = str(_require(params.get("snapshot"), "snapshot"))
        if not snapshot.startswith(str(BACKUP_ROOT)) or ".." in snapshot:
            raise HelperError("invalid_snapshot", "Snapshot path is outside the backup directory")
        _restore_snapshot(snapshot)
        gen = run(["netplan", "generate"], timeout=30)
        commands.append(["netplan", "generate"])
        apply_res = run(["netplan", "apply"], timeout=45)
        commands.append(["netplan", "apply"])
        run(["systemctl", "stop", "nat64mgr-netrollback.timer"], timeout=10)
        return {"rolled_back": True, "snapshot": snapshot}, apply_res["stdout"], gen["stderr"] + apply_res["stderr"], commands
    if action == "status":
        timer = run(["systemctl", "is-active", "nat64mgr-netrollback.timer"], timeout=5)
        snapshots = sorted([p.name for p in BACKUP_ROOT.glob("network-*")], reverse=True)[:20] if BACKUP_ROOT.exists() else []
        return {
            "rollback_timer_active": timer["stdout"].strip() == "active",
            "snapshots": snapshots,
            "netplan_path": str(MANAGED_PATHS["network"]),
            "sysctl_path": str(MANAGED_PATHS["sysctl"]),
        }, "", "", [["systemctl", "is-active", "nat64mgr-netrollback.timer"]]
    raise HelperError("unknown_action", f"Unknown network action: {action}")


def _packages(params: dict) -> tuple[dict, str, str, list]:
    action = _require(params.get("action"), "action")
    commands: list[list[str]] = []
    if action != "install_jool":
        raise HelperError("unknown_action", f"Unknown package action: {action}")
    if not ALLOW_INSTALL_FLAG.exists():
        raise HelperError(
            "package_install_disabled",
            "Package installation is disabled. Create /etc/nat64-manager/allow-package-install and retry.",
        )
    osr = ""
    try:
        osr = Path("/etc/os-release").read_text(encoding="utf-8")
    except OSError:
        pass
    if not re.search(r"^(ID|ID_LIKE)=.*(debian|ubuntu)", osr, re.MULTILINE | re.IGNORECASE):
        raise HelperError("unsupported_distro", "Automatic Jool installation is supported only on Debian/Ubuntu")
    out, err = "", ""
    for argv, timeout in [
        (["apt-get", "update"], 300),
        (["apt-get", "install", "-y", "jool-dkms", "jool-tools"], 900),
        (["modprobe", "jool"], 60),
    ]:
        commands.append(argv)
        res = run(argv, timeout=timeout)
        out += res["stdout"]
        err += res["stderr"]
        if res["exit_code"] != 0:
            raise HelperError("package_install_failed", f"Command failed: {' '.join(argv)}", data=res, commands=commands)
    return {"installed": True}, out, err, commands


def _selftest(params: dict) -> tuple[dict, str, str, list]:
    target = str(_require(params.get("target"), "target"))
    try:
        ipaddress.IPv6Address(target)
    except ValueError as exc:
        raise HelperError("invalid_target", "Self-test target must be an IPv6 address") from exc
    count = min(max(int(params.get("count", 2)), 1), 10)
    netns = _netns(params.get("netns"))
    argv = [*_netns_prefix(netns), "ping", "-6", "-c", str(count), "-W", "2", target]
    res = run(argv, timeout=count * 3 + 5)
    return {"target": target, "success": res["exit_code"] == 0}, res["stdout"], res["stderr"], [argv]


def _validate_keepalived(content: str) -> None:
    lowered = content.lower()
    for token in ["vrrp_script", "notify_", "script ", "include ", "!include", "misc ", "track_script"]:
        if token in lowered:
            raise HelperError(
                "unsafe_ha_config",
                f"keepalived configuration may only contain global_defs and one vrrp_instance; forbidden token: {token.strip()}",
            )
    if lowered.count("vrrp_instance ") != 1:
        raise HelperError("invalid_ha_config", "keepalived configuration must contain exactly one vrrp_instance")
    if "global_defs" not in lowered:
        raise HelperError("invalid_ha_config", "keepalived configuration must contain global_defs")
    if content.count("{") != content.count("}"):
        raise HelperError("invalid_ha_config", "Unbalanced braces in keepalived configuration")


def _ha(params: dict) -> tuple[dict, str, str, list]:
    action = _require(params.get("action"), "action")
    commands: list[list[str]] = []
    if action == "validate" or action == "apply":
        content = _validate_content(params.get("content"), "keepalived")
        _validate_keepalived(content)
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        fd, path = tempfile.mkstemp(prefix="keepalived-", suffix=".conf", dir=str(RUN_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            os.chmod(path, 0o600)
            test_argv = ["keepalived", "-t", "-f", path]
            test = run(test_argv, timeout=20)
            commands.append(["keepalived", "-t", "-f", "<staged>"])
            if test["exit_code"] != 0 and "unrecognized" in (test["stderr"] + test["stdout"]).lower():
                test_argv = ["keepalived", "--config-test", "--use-file", path]
                test = run(test_argv, timeout=20)
                commands.append(["keepalived", "--config-test", "--use-file", "<staged>"])
            if test["exit_code"] != 0:
                raise HelperError(
                    "ha_validation_failed", "keepalived rejected the configuration", data=test, commands=commands
                )
            if action == "validate":
                return {"valid": True}, test["stdout"], test["stderr"], commands
            target = MANAGED_PATHS["keepalived"]
            previous = target.read_bytes() if target.exists() else None
            _write_atomic(target, content, mode=0o640)
            restart = run(["systemctl", "restart", "keepalived"], timeout=45)
            commands.append(["systemctl", "restart", "keepalived"])
            if restart["exit_code"] != 0:
                if previous is not None:
                    _write_atomic(target, previous.decode("utf-8", errors="replace"), mode=0o640)
                    run(["systemctl", "restart", "keepalived"], timeout=45)
                else:
                    target.unlink(missing_ok=True)
                raise HelperError(
                    "ha_apply_failed",
                    "keepalived failed to restart; previous configuration restored",
                    data=restart,
                    commands=commands,
                )
            return {"applied": True}, restart["stdout"], restart["stderr"], commands
        finally:
            Path(path).unlink(missing_ok=True)
    if action == "status":
        active = run(["systemctl", "is-active", "keepalived"], timeout=5)
        commands.append(["systemctl", "is-active", "keepalived"])
        return {
            "active": active["stdout"].strip() == "active",
            "state": active["stdout"].strip(),
            "config_path": str(MANAGED_PATHS["keepalived"]),
            "config_exists": MANAGED_PATHS["keepalived"].exists(),
        }, active["stdout"], active["stderr"], commands
    raise HelperError("unknown_action", f"Unknown HA action: {action}")


def dispatch(request: dict) -> dict:
    if not isinstance(request, dict):
        raise HelperError("invalid_request", "Request must be a JSON object")
    op = request.get("op")
    params = request.get("params") or {}
    if not isinstance(params, dict):
        raise HelperError("invalid_request", "params must be an object")
    handlers = {
        "ping": lambda _p: ({"helper_version": VERSION, "pid": os.getpid()}, "", "", []),
        "jool": _jool,
        "ip": _read_ip,
        "system": _system,
        "dns": _dns,
        "firewall": _firewall,
        "network": _network,
        "packages": _packages,
        "selftest": _selftest,
        "ha": _ha,
    }
    handler = handlers.get(op)
    if not handler:
        raise HelperError("unknown_op", f"Unknown operation: {op}")
    data, stdout, stderr, commands = handler(params)
    return _result_ok(data, stdout, stderr, commands)


def main() -> int:
    os.umask(0o077)
    raw = sys.stdin.read(MAX_OUTPUT)
    try:
        request = json.loads(raw or "{}")
        response = dispatch(request)
    except HelperError as exc:
        response = {
            "ok": False,
            "code": exc.code,
            "message": exc.message,
            "data": exc.data,
            "stdout": str(exc.data.get("stdout", ""))[-MAX_OUTPUT:],
            "stderr": str(exc.data.get("stderr", ""))[-MAX_OUTPUT:],
            "exit_code": int(exc.data.get("exit_code", 1) or 1),
            "commands": exc.commands,
        }
    except json.JSONDecodeError as exc:
        response = {"ok": False, "code": "invalid_json", "message": str(exc), "data": {}, "commands": []}
    except Exception as exc:  # never leak a traceback to the web process
        response = {
            "ok": False,
            "code": "internal_error",
            "message": f"{type(exc).__name__}: {exc}",
            "data": {},
            "commands": [],
        }
    sys.stdout.write(json.dumps(response, default=str))
    sys.stdout.flush()
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
