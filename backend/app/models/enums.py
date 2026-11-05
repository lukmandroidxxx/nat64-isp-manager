from __future__ import annotations

import enum


class RoleName(str, enum.Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


class InterfaceRole(str, enum.Enum):
    WAN4 = "wan4"
    WAN6 = "wan6"
    LAN6 = "lan6"
    LAN4 = "lan4"
    MANAGEMENT = "management"
    OTHER = "other"


class JoolStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    PAUSED = "paused"
    OFFLINE = "offline"
    UNSUPPORTED = "unsupported"
    NOT_INSTALLED = "not_installed"
    VERSION_INCOMPATIBLE = "version_incompatible"
    ERROR = "error"


class SessionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    STALE = "stale"


class Protocol(str, enum.Enum):
    TCP = "tcp"
    UDP = "udp"
    ICMP = "icmp"


class ChangeStatus(str, enum.Enum):
    DRAFT = "draft"
    DRY_RUN_OK = "dry_run_ok"
    DRY_RUN_FAILED = "dry_run_failed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    APPLIED = "applied"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ServiceStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"
    NOT_IMPLEMENTED = "not_implemented"
    ERROR = "error"


class FirewallAction(str, enum.Enum):
    ACCEPT = "accept"
    DROP = "drop"
    REJECT = "reject"
    LOG = "log"


class FirewallFamily(str, enum.Enum):
    INET = "inet"
    IPV4 = "ipv4"
    IPV6 = "ipv6"
