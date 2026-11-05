from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.config import settings
from app.models.enums import JoolStatus, Protocol
from app.services.host import HostError, host
from app.services.jool.parser import (
    JoolInstanceInfo,
    JoolPool4Entry,
    JoolSessionRecord,
    parse_boolean,
    parse_global_display,
    parse_instance_table,
    parse_pool4_csv,
    parse_session_csv,
    parse_stats_csv,
    parse_version,
    version_tuple_str,
)

log = logging.getLogger(__name__)


class JoolError(Exception):
    def __init__(self, code: str, message: str, *, data: dict | None = None, commands: list | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}
        self.commands = commands or []


@dataclass
class JoolStatusInfo:
    status: JoolStatus
    installed: bool
    version: str | None = None
    compatible: bool = False
    message: str | None = None
    capabilities: dict = field(default_factory=dict)
    commands: list = field(default_factory=list)


@dataclass
class JoolObservation:
    name: str
    status: JoolStatus
    paused: bool
    pool6: str | None
    settings: dict[str, str] = field(default_factory=dict)
    stats: dict[str, int] = field(default_factory=dict)
    sessions: list[JoolSessionRecord] = field(default_factory=list)
    session_warnings: list[str] = field(default_factory=list)
    pool4: dict[str, list[JoolPool4Entry]] = field(default_factory=dict)
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    errors: list[str] = field(default_factory=list)


class JoolAdapter:
    def __init__(self) -> None:
        self.netns = settings.jool_netns
        self.min_version = parse_version(settings.jool_min_version) or (4, 0, 0)

    def capabilities(self, force: bool = False) -> dict:
        return host.capabilities(force=force)

    def status(self, force: bool = False) -> JoolStatusInfo:
        caps = self.capabilities(force=force)
        if not caps.get("available"):
            return JoolStatusInfo(
                status=JoolStatus.UNSUPPORTED,
                installed=False,
                message=caps.get("reason") or "Host integration unavailable",
                capabilities=caps,
            )
        binaries = caps.get("binaries") or {}
        if not binaries.get("jool"):
            return JoolStatusInfo(
                status=JoolStatus.NOT_INSTALLED,
                installed=False,
                message="Jool userspace tool is not installed",
                capabilities=caps,
            )
        version_text = caps.get("jool_version")
        version = parse_version(version_text or "")
        if version is None:
            # The capability cache may not have run the version command if it
            # failed; ask directly once.
            try:
                result = self._run("version", timeout=15)
                version_text = result.stdout.strip()
                version = parse_version(version_text)
            except JoolError as exc:
                return JoolStatusInfo(
                    status=JoolStatus.ERROR,
                    installed=True,
                    message=exc.message,
                    capabilities=caps,
                    commands=exc.commands,
                )
        if version is None:
            return JoolStatusInfo(
                status=JoolStatus.ERROR,
                installed=True,
                message=f"Could not parse Jool version from: {version_text!r}",
                capabilities=caps,
            )
        compatible = version[0] == 4 and version >= self.min_version
        return JoolStatusInfo(
            status=JoolStatus.ONLINE if compatible else JoolStatus.VERSION_INCOMPATIBLE,
            installed=True,
            version=version_tuple_str(version),
            compatible=compatible,
            message=None
            if compatible
            else f"Unsupported Jool version {version_tuple_str(version)}; Jool 4.x >= {version_tuple_str(self.min_version)} is required",
            capabilities=caps,
        )

    def _run(self, action: str, *, timeout: int | None = None, **params):
        params.setdefault("netns", self.netns)
        try:
            result = host.call("jool", timeout=timeout, action=action, **params)
        except HostError as exc:
            raise JoolError(exc.code, exc.message, data=exc.data, commands=exc.commands) from exc
        if not result.ok:
            raise JoolError(result.code, result.message, data=result.data, commands=result.commands)
        return result

    def list_instances(self) -> list[JoolInstanceInfo]:
        return parse_instance_table(self._run("instance_display", timeout=20).stdout)

    def version_output(self) -> str:
        return self._run("version", timeout=15).stdout.strip()

    def add_instance(self, name: str, pool6: str, framework: str = "netfilter") -> list:
        result = self._run("instance_add", name=name, pool6=pool6, framework=framework, timeout=40)
        return result.commands

    def remove_instance(self, name: str) -> list:
        result = self._run("instance_remove", name=name, timeout=40)
        return result.commands

    def pause(self, name: str) -> list:
        result = self._run("global_update", instance=name, key="manually-enabled", value="false", timeout=25)
        return result.commands

    def resume(self, name: str) -> list:
        result = self._run("global_update", instance=name, key="manually-enabled", value="true", timeout=25)
        return result.commands

    def update_global(self, name: str, key: str, value: str) -> list:
        result = self._run("global_update", instance=name, key=key, value=value, timeout=25)
        return result.commands

    def pool4_display(self, name: str, protocol: str) -> list[JoolPool4Entry]:
        result = self._run("pool4_display", instance=name, protocol=protocol, timeout=30)
        return parse_pool4_csv(result.stdout, protocol)

    def pool4_add(self, name: str, protocol: str, address: str, port_min: int, port_max: int) -> list:
        result = self._run(
            "pool4_add",
            instance=name,
            protocol=protocol,
            address=address,
            port_min=port_min,
            port_max=port_max,
            timeout=45,
        )
        return result.commands

    def pool4_remove(self, name: str, protocol: str, address: str, port_min: int, port_max: int) -> list:
        result = self._run(
            "pool4_remove",
            instance=name,
            protocol=protocol,
            address=address,
            port_min=port_min,
            port_max=port_max,
            timeout=45,
        )
        return result.commands

    def sessions(self, name: str, protocol: str, timeout: int = 30) -> tuple[list[JoolSessionRecord], list[str], list]:
        result = self._run("session_display", instance=name, protocol=protocol, timeout=timeout)
        records, warnings = parse_session_csv(result.stdout, protocol)
        return records, warnings, result.commands

    def all_sessions(self, name: str) -> tuple[list[JoolSessionRecord], list[str], list]:
        records: list[JoolSessionRecord] = []
        warnings: list[str] = []
        commands: list = []
        for protocol in ("tcp", "udp", "icmp"):
            proto_records, proto_warnings, proto_commands = self.sessions(name, protocol)
            records.extend(proto_records)
            warnings.extend(proto_warnings)
            commands.extend(proto_commands)
        return records, warnings, commands

    def stats(self, name: str) -> tuple[dict[str, int], list]:
        result = self._run("stats_display", instance=name, timeout=25)
        return parse_stats_csv(result.stdout), result.commands

    def global_settings(self, name: str) -> tuple[dict[str, str], list]:
        result = self._run("global_display", instance=name, timeout=20)
        return parse_global_display(result.stdout), result.commands

    def observe(self, name: str, *, include_sessions: bool = True) -> JoolObservation:
        status = self.status()
        if status.status not in {JoolStatus.ONLINE, JoolStatus.PAUSED}:
            return JoolObservation(
                name=name,
                status=status.status,
                paused=False,
                pool6=None,
                errors=[status.message] if status.message else [],
            )
        errors: list[str] = []
        commands: list = []
        try:
            instances = self.list_instances()
        except JoolError as exc:
            return JoolObservation(
                name=name, status=JoolStatus.ERROR, paused=False, pool6=None, errors=[exc.message]
            )
        match = next((item for item in instances if item.name == name), None)
        if not match:
            return JoolObservation(
                name=name,
                status=JoolStatus.OFFLINE,
                paused=False,
                pool6=None,
                errors=[f"Instance {name!r} is not present in Jool"],
            )
        try:
            settings_map, cmd = self.global_settings(name)
            commands.extend(cmd)
        except JoolError as exc:
            settings_map = {}
            errors.append(exc.message)
        try:
            stats_map, cmd = self.stats(name)
            commands.extend(cmd)
        except JoolError as exc:
            stats_map = {}
            errors.append(exc.message)
        pool4: dict[str, list[JoolPool4Entry]] = {}
        for proto in ("tcp", "udp", "icmp"):
            try:
                pool4[proto] = self.pool4_display(name, proto)
            except JoolError as exc:
                errors.append(f"pool4 {proto}: {exc.message}")
        sessions: list[JoolSessionRecord] = []
        warnings: list[str] = []
        if include_sessions:
            try:
                sessions, warnings, cmd = self.all_sessions(name)
                commands.extend(cmd)
            except JoolError as exc:
                errors.append(f"sessions: {exc.message}")
        paused = not parse_boolean(settings_map.get("manually-enabled"), default=True)
        return JoolObservation(
            name=name,
            status=JoolStatus.PAUSED if paused else JoolStatus.ONLINE,
            paused=paused,
            pool6=settings_map.get("pool6") or None,
            settings=settings_map,
            stats=stats_map,
            sessions=sessions,
            session_warnings=warnings,
            pool4=pool4,
            errors=errors,
        )

    @staticmethod
    def protocol_list() -> tuple[str, ...]:
        return tuple(p.value for p in Protocol)


jool = JoolAdapter()
