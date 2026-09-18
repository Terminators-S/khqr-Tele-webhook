import hashlib
import re
from dataclasses import dataclass


TRX_RE = re.compile(r"(?:Trx\.?\s*ID:|លេខប្រតិបត្តិការ:|Transaction\s*ID:|Txn\s*ID:|ID:)\s*([A-Za-z0-9-]{8,})", re.I)
AMOUNT_RE = re.compile(r"(?:\$|USD\s*)(\d+(?:\.\d{1,2})?)|(\d+(?:\.\d{1,2})?)\s*USD", re.I)
REMARK_RE = re.compile(r"(?:Remark|Reference|Ref(?:erence)?|ចំណាំ)\s*[:：-]\s*([A-Za-z0-9_-]{4,64})", re.I)


@dataclass(frozen=True)
class ParsedEvidence:
    trx_id: str | None
    amount_minor: int | None
    currency: str
    remark: str | None


def parse_aba_text(text: str) -> ParsedEvidence:
    trx = TRX_RE.search(text or "")
    amount = AMOUNT_RE.search(text or "")
    remark = REMARK_RE.search(text or "")
    amount_minor = None
    if amount:
        raw = amount.group(1) or amount.group(2)
        whole, _, frac = raw.partition(".")
        amount_minor = int(whole) * 100 + int((frac + "00")[:2])
    return ParsedEvidence(
        trx_id=trx.group(1) if trx else None,
        amount_minor=amount_minor,
        currency="USD",
        remark=remark.group(1).upper() if remark else None,
    )


def evidence_fingerprint(source_id: str, transport: str, message_id: str, raw_text: str) -> str:
    value = "\x1f".join((source_id, transport, message_id, raw_text))
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
