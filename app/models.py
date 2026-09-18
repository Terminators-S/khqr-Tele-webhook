import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid.uuid4())


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    webhook_url: Mapped[str | None] = mapped_column(String(800), nullable=True)
    webhook_secret: Mapped[str | None] = mapped_column(String(160), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PaymentSource(Base):
    __tablename__ = "payment_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    telegram_group_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    telegram_sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    merchant_alias: Mapped[str | None] = mapped_column(String(160), nullable=True)
    static_khqr: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PaymentIntent(Base):
    __tablename__ = "payment_intents"
    __table_args__ = (
        UniqueConstraint("business_id", "idempotency_key", name="uq_intent_business_idempotency"),
        UniqueConstraint("business_id", "external_id", name="uq_intent_business_external"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("payment_sources.id", ondelete="RESTRICT"), index=True)
    external_id: Mapped[str] = mapped_column(String(180), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    base_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True, nullable=False)
    paid_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    excess_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    checkout_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    history_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentRequest(Base):
    __tablename__ = "payment_requests"
    __table_args__ = (UniqueConstraint("source_id", "remark", name="uq_request_source_remark"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    intent_id: Mapped[str] = mapped_column(ForeignKey("payment_intents.id", ondelete="CASCADE"), unique=True, index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("payment_sources.id", ondelete="RESTRICT"), index=True)
    remark: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    offset_minor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payable_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    checkout_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    match_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AmountReservation(Base):
    __tablename__ = "amount_reservations"
    __table_args__ = (
        UniqueConstraint("source_id", "currency", "payable_amount_minor", name="uq_reservation_payable"),
        UniqueConstraint("request_id", name="uq_reservation_request"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    request_id: Mapped[str] = mapped_column(ForeignKey("payment_requests.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("payment_sources.id", ondelete="CASCADE"), index=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    base_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    payable_amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    offset_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class PaymentEvidence(Base):
    __tablename__ = "payment_evidence"
    __table_args__ = (
        UniqueConstraint("source_id", "trx_id", name="uq_evidence_source_trx"),
        UniqueConstraint("source_id", "transport", "transport_message_id", name="uq_evidence_transport_message"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("payment_sources.id", ondelete="CASCADE"), index=True)
    transport: Mapped[str] = mapped_column(String(32), nullable=False)
    transport_message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trx_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    remark: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    state: Mapped[str] = mapped_column(String(32), default="RECEIVED", index=True, nullable=False)
    quarantine_reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    matched_request_id: Mapped[str | None] = mapped_column(ForeignKey("payment_requests.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"
    __table_args__ = (UniqueConstraint("evidence_id", name="uq_allocation_evidence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("payment_evidence.id", ondelete="RESTRICT"), index=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("payment_intents.id", ondelete="CASCADE"), index=True)
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PaymentClaim(Base):
    __tablename__ = "payment_claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    intent_id: Mapped[str] = mapped_column(ForeignKey("payment_intents.id", ondelete="CASCADE"), index=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("payment_evidence.id", ondelete="RESTRICT"), index=True)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    intent_id: Mapped[str | None] = mapped_column(ForeignKey("payment_intents.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class WebhookOutbox(Base):
    __tablename__ = "webhook_outbox"
    __table_args__ = (UniqueConstraint("event_id", name="uq_outbox_event"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(ForeignKey("payment_events.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[str] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True, nullable=False)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class SourceCursor(Base):
    __tablename__ = "source_cursors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("payment_sources.id", ondelete="CASCADE"), unique=True, index=True)
    last_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
