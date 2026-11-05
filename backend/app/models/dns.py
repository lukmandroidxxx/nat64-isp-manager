from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Dns64Settings(Base, TimestampMixin):
    __tablename__ = "dns64_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String(64), default="64:ff9b::/96", nullable=False)
    upstreams: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    listen_address: Mapped[str] = mapped_column(String(64), default="127.0.0.1", nullable=False)
    service_name: Mapped[str] = mapped_column(String(32), default="named", nullable=False)
    managed_config_path: Mapped[str] = mapped_column(String(255), default="/etc/bind/nat64-manager.conf", nullable=False)
    test_domain: Mapped[str] = mapped_column(String(255), default="ipv4only.arpa", nullable=False)
    exclude_prefixes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    forward_only: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    dnssec_validation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_synthesis_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_synthesis_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synthesis_detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    config_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_render_hash: Mapped[str | None] = mapped_column(String(64))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
