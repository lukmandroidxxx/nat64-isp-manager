from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)


class HostError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        data: dict | None = None,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 1,
        commands: list | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.commands = commands or []

    def as_dict(self) -> dict:
        return {
            "ok": False,
            "code": self.code,
            "message": self.message,
            "data": self.data,
            "stdout": self.stdout[-settings.command_log_limit :],
            "stderr": self.stderr[-settings.command_log_limit :],
            "exit_code": self.exit_code,
            "commands": self.commands,
        }


@dataclass
class HostResult:
    ok: bool
    code: str = "ok"
    message: str = ""
    data: dict = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    commands: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict) -> HostResult:
        return cls(
            ok=bool(value.get("ok")),
            code=str(value.get("code", "ok")),
            message=str(value.get("message", "")),
            data=value.get("data") or {},
            stdout=str(value.get("stdout", "")),
            stderr=str(value.get("stderr", "")),
            exit_code=int(value.get("exit_code", 0) or 0),
            commands=value.get("commands") or [],
        )

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "data": self.data,
            "stdout": self.stdout[-settings.command_log_limit :],
            "stderr": self.stderr[-settings.command_log_limit :],
            "exit_code": self.exit_code,
            "commands": self.commands,
        }


class HostClient:
    """Strict JSON-stdin transport to the root-owned host helper."""

    def __init__(self) -> None:
        self._caps: dict | None = None
        self._caps_at = 0.0
        self._lock = threading.Lock()

    @property
    def platform_supported(self) -> bool:
        return platform.system().lower() == "linux"

    @property
    def mode(self) -> str:
        if settings.host_control_mode == "disabled":
            return "disabled"
        if not self.platform_supported:
            return "unsupported"
        if settings.host_control_mode in {"direct", "sudo"}:
            return settings.host_control_mode
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return "direct"
        return "sudo" if settings.hostctl_use_sudo else "direct"

    @property
    def available(self) -> bool:
        if self.mode in {"disabled", "unsupported"}:
            return False
        return os.path.isfile(settings.hostctl_path)

    def unavailable_reason(self) -> str | None:
        if not self.platform_supported:
            return f"Host control requires Linux; this server is {platform.system()}"
        if settings.host_control_mode == "disabled":
            return "Host control is disabled by configuration"
        if not os.path.isfile(settings.hostctl_path):
            return f"Privileged helper not found at {settings.hostctl_path}"
        return None

    def _command(self) -> list[str]:
        mode = self.mode
        python = settings.hostctl_python
        if os.name == "nt":
            python = sys.executable
        if mode == "sudo":
            return ["sudo", "-n", python, settings.hostctl_path]
        return [python, settings.hostctl_path]

    def call(self, op: str, timeout: int | None = None, **params: Any) -> HostResult:
        if not self.available:
            raise HostError(
                "host_unsupported",
                self.unavailable_reason() or "Host control unavailable",
            )
        request = json.dumps({"op": op, "params": params}, separators=(",", ":"))
        argv = self._command()
        try:
            proc = subprocess.run(
                argv,
                input=request,
                text=True,
                capture_output=True,
                timeout=timeout or settings.hostctl_timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            log.error("hostctl timeout op=%s", op)
            raise HostError("host_timeout", f"Host helper timed out during {op}") from exc
        except FileNotFoundError as exc:
            raise HostError("helper_unavailable", f"Cannot execute host helper: {exc}") from exc
        except OSError as exc:
            raise HostError("helper_unavailable", f"Cannot execute host helper: {exc}") from exc

        raw = (proc.stdout or "").strip()
        if not raw:
            raise HostError(
                "helper_no_output",
                "Host helper returned no output",
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                exit_code=proc.returncode,
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HostError(
                "helper_invalid_output",
                "Host helper returned invalid JSON (is sudo allowed?)",
                stdout=raw[:4000],
                stderr=proc.stderr or "",
                exit_code=proc.returncode,
            ) from exc
        result = HostResult.from_dict(payload)
        if not result.ok:
            log.warning("hostctl op=%s failed code=%s: %s", op, result.code, result.message)
        return result

    def call_or_raise(self, op: str, timeout: int | None = None, **params: Any) -> HostResult:
        result = self.call(op, timeout=timeout, **params)
        if not result.ok:
            raise HostError(
                result.code,
                result.message,
                data=result.data,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                commands=result.commands,
            )
        return result

    def ping(self) -> bool:
        try:
            return self.call("ping", timeout=10).ok
        except HostError:
            return False

    def capabilities(self, force: bool = False) -> dict:
        if not self.available:
            return {
                "available": False,
                "mode": self.mode,
                "reason": self.unavailable_reason(),
                "platform": platform.system(),
                "is_linux": self.platform_supported,
                "jool_installed": False,
                "jool_version": None,
            }
        with self._lock:
            if force or not self._caps or time.monotonic() - self._caps_at > 30:
                try:
                    result = self.call_or_raise("system", action="capabilities", timeout=30)
                    self._caps = {**result.data, "available": True, "mode": self.mode, "reason": None}
                    self._caps_at = time.monotonic()
                except HostError as exc:
                    self._caps = {
                        "available": False,
                        "mode": self.mode,
                        "reason": exc.message,
                        "platform": platform.system(),
                        "is_linux": self.platform_supported,
                        "jool_installed": False,
                        "jool_version": None,
                    }
            return dict(self._caps)

    def jool(self, action: str, **params: Any) -> HostResult:
        return self.call("jool", action=action, **params)

    def ip(self, kind: str, **params: Any) -> HostResult:
        return self.call("ip", kind=kind, **params)

    def dns(self, action: str, timeout: int | None = None, **params: Any) -> HostResult:
        return self.call("dns", action=action, timeout=timeout, **params)

    def firewall(self, action: str, **params: Any) -> HostResult:
        return self.call("firewall", action=action, **params)

    def network(self, action: str, timeout: int | None = None, **params: Any) -> HostResult:
        return self.call("network", action=action, timeout=timeout, **params)

    def system(self, action: str, **params: Any) -> HostResult:
        return self.call("system", action=action, **params)

    def selftest(self, target: str, count: int = 2, netns: str | None = None) -> HostResult:
        return self.call("selftest", target=target, count=count, netns=netns)


host = HostClient()
