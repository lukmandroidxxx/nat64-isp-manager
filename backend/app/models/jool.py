from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, BigIntPK, TimestampMixin, utcnow

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a circular import
    from app.models.network import IPv4Pool


class JoolInstance(Base, TimestampMixin):
    __tablename__ = "jool_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(15), unique=True, nullable=False, index=True)
    framework: Mapped[str] = mapped_column(String(16), default="netfilter", nullable=False)
    pool6: Mapped[str] = mapped_column(String(64), nullable=False)
    namespace: Mapped[str | None] = mapped_column(String(64))
    display_name: Mapped[str | None] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    jool_version: Mapped[str | None] = mapped_column(String(32))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    observed_pool6: Mapped[str | None] = mapped_column(String(64))
    observed_pool4: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    observed_stats: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    config_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    sessions: Mapped[list[Nat64Session]] = relationship(
        back_populates="instance", cascade="all, delete-orphan"
    )
    pools: Mapped[list[IPv4Pool]] = relationship(back_populates="jool_instance")


class Nat64Session(Base):
    __tablename__ = "nat64_sessions"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "protocol",
            "client_ipv6",
            "client_port",
            "translated_ipv4",
            "translated_port",
            "remote_ipv4",
            "remote_port",
            name="uq_nat64_session_tuple",
        ),
        Index("ix_nat64_sessions_active", "status", "protocol"),
        Index("ix_nat64_sessions_translated", "translated_ipv4", "status"),
        Index("ix_nat64_sessions_client", "client_ipv6", "status"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("jool_instances.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pool4_address_id: Mapped[int | None] = mapped_column(
        ForeignKey("ipv4_addresses.id", ondelete="SET NULL"), index=True
    )
    protocol: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    client_ipv6: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    client_port: Mapped[int | None] = mapped_column(Integer)
    translated_ipv4: Mapped[str] = mapped_column(String(43), nullable=False, index=True)
    translated_port: Mapped[int | None] = mapped_column(Integer)
    remote_ipv4: Mapped[str] = mapped_column(String(43), nullable=False, index=True)
    remote_port: Mapped[int | None] = mapped_column(Integer)
    expires_in_seconds: Mapped[int | None] = mapped_column(Integer)
    jool_state: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    instance: Mapped[JoolInstance] = relationship(back_populates="sessions")
    pool4_address = relationship("IPv4Address", back_populates="sessions")
