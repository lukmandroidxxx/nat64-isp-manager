from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime


class JoolParseError(ValueError):
    pass


def parse_version(output: str) -> tuple[int, int, int] | None:
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", output or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def version_tuple_str(version: tuple[int, int, int] | None) -> str | None:
    if not version:
        return None
    return ".".join(str(part) for part in version)


def parse_duration(value: str | None) -> int | None:
    """Parse Jool's HH:MM:SS[.mmm] / MM:SS[.mmm] display."""
    if not value:
        return None
    text = value.strip()
    if not text or text in {"-", "N/A"}:
        return None
    days = 0
    day_match = re.search(r"(\d+)\s*days?\b", text, re.IGNORECASE)
    if day_match:
        days = int(day_match.group(1))
        text = (text[: day_match.start()] + text[day_match.end() :]).strip()
        text = text.lstrip(",").strip()
        if not text:
            return days * 86400
    parts = text.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            seconds_total = int(hours) * 3600 + int(minutes) * 60 + int(float(seconds))
        elif len(parts) == 2:
            minutes, seconds = parts
            seconds_total = int(minutes) * 60 + int(float(seconds))
        elif len(parts) == 1:
            seconds_total = int(float(parts[0]))
        else:
            return None
    except (TypeError, ValueError):
        return None
    return days * 86400 + seconds_total


@dataclass
class JoolPool4Entry:
    address: str
    port_min: int
    port_max: int
    protocol: str
    mark: int = 0
    max_iterations: str | None = None

    @property
    def capacity(self) -> int:
        return max(0, self.port_max - self.port_min + 1)


@dataclass
class JoolSessionRecord:
    protocol: str
    client_ipv6: str
    client_port: int | None
    translated_ipv4: str
    translated_port: int | None
    remote_ipv4: str
    remote_port: int | None
    expires_in_seconds: int | None = None
    jool_state: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def key(self) -> tuple:
        return (
            self.protocol,
            self.client_ipv6,
            self.client_port,
            self.translated_ipv4,
            self.translated_port,
            self.remote_ipv4,
            self.remote_port,
        )


@dataclass
class JoolInstanceInfo:
    name: str
    framework: str
    namespace_hash: str | None = None


def _to_int(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"-", "N/A"}:
        return None
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return None


def parse_instance_table(output: str) -> list[JoolInstanceInfo]:
    instances: list[JoolInstanceInfo] = []
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        if cells[1].lower() == "name" or cells[-1].lower() == "framework":
            continue
        if set(cells[1]) <= {"-"}:
            continue
        instances.append(
            JoolInstanceInfo(namespace_hash=cells[0] or None, name=cells[1], framework=cells[2].lower())
        )
    return instances


def _read_csv(output: str) -> tuple[list[str], list[list[str]]]:
    text = (output or "").strip()
    if not text:
        return [], []
    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if row and any(cell.strip() for cell in row)]
    if not rows:
        return [], []
    header = [cell.strip().lower().replace("_", " ") for cell in rows[0]]
    return header, rows[1:]


def parse_pool4_csv(output: str, protocol: str) -> list[JoolPool4Entry]:
    """Parse `jool pool4 display --<proto> --csv`.

    Header names have varied slightly across Jool releases, so match columns by
    normalized name with sensible fallbacks.
    """
    header, rows = _read_csv(output)
    entries: list[JoolPool4Entry] = []
    if not header:
        return entries

    def find(*names: str) -> int | None:
        for name in names:
            normalized = name.lower().replace("_", " ")
            if normalized in header:
                return header.index(normalized)
        return None

    address_idx = find("address", "ipv4 address", "ipv4 prefix")
    ports_idx = find("ports", "port range", "port")
    mark_idx = find("mark")
    max_idx = find("max iterations", "max iteration")
    if address_idx is None:
        # Some versions emit rows without a header (--no-headers) or with a
        # header we do not recognize. Scan every line for an IPv4 token and a
        # port range so headerless output still parses.
        ip_pattern = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?")
        range_pattern = re.compile(r"\d+\s*-\s*\d+")
        for line in (output or "").splitlines():
            tokens = [token for token in re.split(r"[,\s]+", line.strip()) if token]
            candidate = next((token for token in tokens if ip_pattern.fullmatch(token)), None)
            if not candidate:
                continue
            range_cell = next((token for token in tokens if range_pattern.fullmatch(token)), None)
            if not range_cell:
                continue
            try:
                entries.append(_pool4_entry(candidate, range_cell, protocol, 0, None))
            except JoolParseError:
                continue
        return entries

    for row in rows:
        if len(row) <= address_idx:
            continue
        address = row[address_idx].strip()
        if not address:
            continue
        range_cell = row[ports_idx].strip() if ports_idx is not None and len(row) > ports_idx else "0-65535"
        mark = _to_int(row[mark_idx]) if mark_idx is not None and len(row) > mark_idx else 0
        max_iter = row[max_idx].strip() if max_idx is not None and len(row) > max_idx else None
        try:
            entries.append(_pool4_entry(address, range_cell, protocol, mark or 0, max_iter))
        except JoolParseError:
            continue
    return entries


def _pool4_entry(address: str, range_cell: str, protocol: str, mark: int, max_iter: str | None) -> JoolPool4Entry:
    range_match = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", range_cell)
    if not range_match:
        single = re.fullmatch(r"\s*(\d+)\s*", range_cell)
        if single:
            lo = hi = int(single.group(1))
        else:
            raise JoolParseError(f"Invalid port range: {range_cell}")
    else:
        lo, hi = int(range_match.group(1)), int(range_match.group(2))
    if not (0 <= lo <= hi <= 65535):
        raise JoolParseError(f"Invalid pooled port range: {range_cell}")
    return JoolPool4Entry(
        address=address,
        port_min=lo,
        port_max=hi,
        protocol=protocol.lower(),
        mark=mark,
        max_iterations=max_iter,
    )


def parse_session_csv(output: str, protocol: str) -> tuple[list[JoolSessionRecord], list[str]]:
    header, rows = _read_csv(output)
    warnings: list[str] = []
    records: list[JoolSessionRecord] = []
    if not rows:
        return records, warnings
    normalized = [h.replace("_", " ").strip() for h in header]

    def col(*names: str) -> int | None:
        for name in names:
            if name in normalized:
                return normalized.index(name)
        return None

    c_proto = col("protocol")
    c_v6 = col("ipv6 remote address", "ipv6 address", "source ipv6")
    c_v6_port = col("ipv6 remote l4 id", "ipv6 remote port", "ipv6 l4 id")
    c_v4_local = col("ipv4 local address", "translated ipv4", "ipv4 mask address")
    c_v4_local_port = col("ipv4 local l4 id", "ipv4 local port", "ipv4 mask port")
    c_v4_remote = col("ipv4 remote address", "destination ipv4")
    c_v4_remote_port = col("ipv4 remote l4 id", "ipv4 remote port", "destination port")
    c_expires = col("expires in", "expires", "time to expiration")
    c_state = col("state", "tcp state")

    if c_v6 is None or c_v4_local is None or c_v4_remote is None:
        warnings.append("Unrecognized Jool session CSV header; no sessions parsed")
        return records, warnings

    for line_no, row in enumerate(rows, start=2):
        if len(row) <= max(c_v6, c_v4_local, c_v4_remote):
            warnings.append(f"Session row {line_no}: too few columns")
            continue
        row_proto = (row[c_proto].strip().lower() if c_proto is not None and len(row) > c_proto else protocol)
        if row_proto not in {"tcp", "udp", "icmp"}:
            warnings.append(f"Session row {line_no}: unknown protocol {row_proto}")
            continue
        client_v6 = row[c_v6].strip()
        translated = row[c_v4_local].strip()
        remote = row[c_v4_remote].strip()
        if not client_v6 or not translated or not remote:
            warnings.append(f"Session row {line_no}: empty address")
            continue
        expires = None
        if c_expires is not None and len(row) > c_expires:
            expires = parse_duration(row[c_expires])
        state = row[c_state].strip() if c_state is not None and len(row) > c_state else None
        records.append(
            JoolSessionRecord(
                protocol=row_proto,
                client_ipv6=client_v6,
                client_port=_to_int(row[c_v6_port]) if c_v6_port is not None and len(row) > c_v6_port else None,
                translated_ipv4=translated,
                translated_port=(
                    _to_int(row[c_v4_local_port]) if c_v4_local_port is not None and len(row) > c_v4_local_port else None
                ),
                remote_ipv4=remote,
                remote_port=(
                    _to_int(row[c_v4_remote_port]) if c_v4_remote_port is not None and len(row) > c_v4_remote_port else None
                ),
                expires_in_seconds=expires,
                jool_state=state or None,
                raw={normalized[i]: row[i] for i in range(min(len(normalized), len(row)))},
            )
        )
    return records, warnings


def parse_stats_csv(output: str) -> dict[str, int]:
    header, rows = _read_csv(output)
    result: dict[str, int] = {}
    if rows and header and header[0] == "stat":
        value_idx = header.index("value") if "value" in header else 1
        for row in rows:
            if len(row) <= value_idx:
                continue
            key = row[0].strip()
            value = _to_int(row[value_idx])
            if key and value is not None:
                result[key] = value
        return result
    for line in (output or "").splitlines():
        match = re.match(r"^\s*([A-Z0-9_]+)\s*[:=,]\s*(-?\d+)", line)
        if match:
            result[match.group(1)] = int(match.group(2))
    return result


def parse_global_display(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (output or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if re.fullmatch(r"[a-z0-9-]+", key):
            values[key] = value.strip()
    return values


def parse_boolean(value: str | None, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"true", "1", "yes", "on", "enabled"}


def now_utc() -> datetime:
    return datetime.now(UTC)
