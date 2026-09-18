import hashlib
import hmac
import json
import time
from typing import Any

import httpx


class KhqrApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"KHQR API {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class KhqrClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            transport=transport,
            headers={"X-Api-Key": api_key},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.is_error:
            detail = payload.get("detail") if isinstance(payload, dict) else None
            raise KhqrApiError(response.status_code, str(detail or response.text or "request failed"))
        if not isinstance(payload, dict):
            raise KhqrApiError(response.status_code, "unexpected response payload")
        return payload

    def create_payment_intent(
        self,
        *,
        source_id: str,
        external_id: str,
        amount_minor: int,
        idempotency_key: str,
        currency: str = "USD",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._client.post(
            "/v1/payment-intents",
            headers={"Idempotency-Key": idempotency_key},
            json={
                "source_id": source_id,
                "external_id": external_id,
                "amount_minor": amount_minor,
                "currency": currency,
                "metadata": metadata or {},
            },
        )
        return self._json(response)


    def get_payment_intent(self, intent_id: str) -> dict[str, Any]:
        return self._json(self._client.get(f"/v1/payment-intents/{intent_id}"))

    def recover_payment_intent(self, intent_id: str, trx_id: str) -> dict[str, Any]:
        return self._json(
            self._client.post(
                f"/v1/payment-intents/{intent_id}/recover",
                json={"trx_id": trx_id},
            )
        )


def verify_webhook_signature(
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    *,
    tolerance_seconds: int = 300,
    now: int | None = None,
) -> bool:
    try:
        timestamp_int = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = int(time.time()) if now is None else int(now)
    if tolerance_seconds >= 0 and abs(current - timestamp_int) > tolerance_seconds:
        return False
    supplied = (signature or "").removeprefix("v1=")
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(supplied, expected)


def decode_webhook(body: bytes) -> dict[str, Any]:
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise ValueError("Webhook payload must be a JSON object.")
    return payload
