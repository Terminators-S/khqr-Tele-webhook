from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class BusinessCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,118}[a-z0-9]$")
    webhook_url: str | None = None


class BusinessCreated(BaseModel):
    id: str
    name: str
    slug: str
    api_key: str
    webhook_secret: str | None = None


class PaymentSourceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    currency: str = "USD"
    telegram_group_id: int | None = None
    telegram_sender_id: int | None = None
    merchant_alias: str | None = None
    static_khqr: str | None = None
    enabled: bool = False


class PaymentSourceOut(BaseModel):
    id: str
    business_id: str
    name: str
    currency: str
    telegram_group_id: int | None
    telegram_sender_id: int | None
    merchant_alias: str | None
    static_khqr: str | None
    enabled: bool
    ready: bool


class PaymentSourceSenderUpdate(BaseModel):
    telegram_sender_id: int


class PaymentSourceEnabledUpdate(BaseModel):
    enabled: bool


class PaymentIntentCreate(BaseModel):
    source_id: str
    external_id: str = Field(min_length=1, max_length=180)
    amount_minor: int = Field(gt=0)
    currency: str = "USD"
    metadata: dict[str, Any] = Field(default_factory=dict)
    remark_prefix: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{2,6}$")


class PaymentRequestOut(BaseModel):
    id: str
    remark: str
    mode: str
    offset_minor: int | None
    payable_amount_minor: int
    static_khqr: str
    checkout_expires_at: datetime
    match_expires_at: datetime


class PaymentIntentOut(BaseModel):
    id: str
    external_id: str
    source_id: str
    amount_minor: int
    currency: str
    status: str
    paid_minor: int
    excess_minor: int
    created_at: datetime
    checkout_expires_at: datetime
    payment_request: PaymentRequestOut


class EvidenceIn(BaseModel):
    source_id: str
    transport: str = "telegram"
    transport_message_id: str
    sender_id: int | None = None
    raw_text: str
    received_at: datetime | None = None
    trx_id: str | None = None
    amount_minor: int | None = None
    currency: str = "USD"
    remark: str | None = None


class EvidenceOut(BaseModel):
    id: str
    trx_id: str
    state: str
    quarantine_reason: str | None
    matched_request_id: str | None


class RecoveryRequest(BaseModel):
    trx_id: str = Field(min_length=8, max_length=128)


class ShadowPromotionRequest(BaseModel):
    limit: int = Field(default=1000, ge=1, le=5000)
