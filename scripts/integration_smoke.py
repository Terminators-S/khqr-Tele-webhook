#!/usr/bin/env python3
"""No-money end-to-end smoke for the deployed KHQR core.

The smoke creates an isolated client/source, creates an intent through the SDK,
injects synthetic RECEIVED evidence through the internal API, waits for the
real settlement worker, and verifies the real webhook worker's HMAC callback.
It never starts Telegram and never enables either Telegram safety fuse.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from khqr_sdk import KhqrClient, decode_webhook, verify_webhook_signature

try:
    from scripts.onboard_consuming_project import onboard_project
except ModuleNotFoundError:  # direct `python scripts/integration_smoke.py`
    from onboard_consuming_project import onboard_project


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API_URL = "http://127.0.0.1:8088"
class WebhookCapture:
    def __init__(self) -> None:
        self.secret: str | None = None
        self.events: list[dict[str, Any]] = []
        self.condition = threading.Condition()

    def set_secret(self, secret: str) -> None:
        with self.condition:
            self.secret = secret
            self.condition.notify_all()

    def record(self, record: dict[str, Any]) -> None:
        with self.condition:
            self.events.append(record)
            self.condition.notify_all()

    def wait_for_paid(self, intent_id: str, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for record in self.events:
                    payload = record.get("payload") or {}
                    data = payload.get("data") or {}
                    if (
                        record.get("valid") is True
                        and payload.get("type") == "payment.intent.paid"
                        and data.get("intent_id") == intent_id
                    ):
                        return record
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("signed payment.intent.paid webhook was not received")
                self.condition.wait(min(remaining, 0.5))


class _WebhookHandler(BaseHTTPRequestHandler):
    capture: WebhookCapture

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        timestamp = self.headers.get("X-KHQR-Timestamp", "")
        signature = self.headers.get("X-KHQR-Signature", "")
        event_id = self.headers.get("X-KHQR-Event-ID", "")
        secret = self.capture.secret

        valid = bool(secret) and verify_webhook_signature(
            secret or "",
            timestamp,
            body,
            signature,
            tolerance_seconds=300,
        )
        try:
            payload = decode_webhook(body)
        except (ValueError, json.JSONDecodeError):
            payload = {}
            valid = False
        if payload.get("id") != event_id:
            valid = False

        self.capture.record(
            {
                "valid": valid,
                "event_id": event_id,
                "payload": payload,
            }
        )
        self.send_response(204 if valid else 401)
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        return


def start_webhook_receiver() -> tuple[ThreadingHTTPServer, WebhookCapture, threading.Thread]:
    capture = WebhookCapture()
    handler = type("KhqrSmokeWebhookHandler", (_WebhookHandler,), {})
    handler.capture = capture
    server = ThreadingHTTPServer(("0.0.0.0", 0), handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="khqr-smoke-webhook",
        daemon=True,
    )
    thread.start()
    return server, capture, thread


def docker_webhook_gateway() -> str:
    container_id = subprocess.check_output(
        ["docker", "compose", "ps", "-q", "webhook"],
        cwd=REPO_ROOT,
        text=True,
    ).strip()
    if not container_id:
        raise RuntimeError("docker compose webhook worker is not running")
    info = json.loads(
        subprocess.check_output(["docker", "inspect", container_id], text=True)
    )[0]
    networks = info.get("NetworkSettings", {}).get("Networks", {})
    for config in networks.values():
        gateway = config.get("Gateway")
        if gateway:
            return str(gateway)
    raise RuntimeError("could not determine webhook worker Docker gateway")


def require_json(response: httpx.Response) -> dict[str, Any]:
    if response.is_error:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise RuntimeError(f"API {response.status_code}: {detail}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("API returned a non-object response")
    return payload


def wait_for_paid(
    client: KhqrClient,
    intent_id: str,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        intent = client.get_payment_intent(intent_id)
        if intent.get("status") == "PAID":
            return intent
        time.sleep(0.2)
    raise TimeoutError("settlement worker did not move intent to PAID")


def assert_safe_runtime() -> None:
    settings = get_settings()
    problems: list[str] = []
    if not settings.telegram_shadow_only:
        problems.append("TELEGRAM_SHADOW_ONLY must remain true")
    if settings.allow_live_telegram:
        problems.append("ALLOW_LIVE_TELEGRAM must remain false")
    if settings.allow_shadow_promotion:
        problems.append("ALLOW_SHADOW_PROMOTION must remain false")
    if problems:
        raise RuntimeError("; ".join(problems))


def run_smoke(api_url: str, timeout: float) -> dict[str, Any]:
    assert_safe_runtime()
    settings = get_settings()
    internal_secret = os.getenv("INTERNAL_SECRET") or settings.internal_secret
    suffix = uuid.uuid4().hex[:12]
    numeric = int(suffix[:8], 16)
    project_slug = f"smoke-{suffix}"
    external_id = f"order-{suffix}"
    trx_id = f"SMOKE{suffix.upper()}"
    message_id = f"synthetic-{suffix}"
    server, capture, thread = start_webhook_receiver()
    try:
        gateway = docker_webhook_gateway()
        webhook_url = f"http://{gateway}:{server.server_port}/khqr-smoke"
        onboarded = onboard_project(
            api_base_url=api_url,
            internal_secret=internal_secret,
            project_name=f"KHQR Smoke {suffix}",
            project_slug=project_slug,
            source_name="Synthetic Smoke Source",
            currency="USD",
            merchant_alias=f"SMOKE {suffix.upper()}",
            static_khqr=f"SMOKE-STATIC-KHQR-{suffix}",
            telegram_group_id=-(1_000_000_000_000 + numeric),
            telegram_sender_id=100_000 + (numeric % 900_000_000),
            webhook_url=webhook_url,
        )
        api_key = str(onboarded["credentials"]["api_key"])
        webhook_secret = str(onboarded["credentials"]["webhook_secret"])
        if not webhook_secret:
            raise RuntimeError("onboarding did not return a webhook secret")
        capture.set_secret(webhook_secret)

        source_id = str(onboarded["source_id"])
        with KhqrClient(api_url, api_key) as client:
            intent = client.create_payment_intent(
                source_id=source_id,
                external_id=external_id,
                amount_minor=500,
                idempotency_key=f"idem-{suffix}",
                currency="USD",
                metadata={"smoke": True, "suffix": suffix},
            )
            intent_id = str(intent["id"])
            request = intent["payment_request"]
            payable_minor = int(request["payable_amount_minor"])
            remark = str(request["remark"])

            with httpx.Client(
                base_url=api_url,
                headers={"X-Internal-Secret": internal_secret},
                timeout=10.0,
            ) as internal:
                evidence = require_json(
                    internal.post(
                        "/internal/evidence",
                        json={
                            "source_id": source_id,
                            "transport": "synthetic-smoke",
                            "transport_message_id": message_id,
                            "sender_id": None,
                            "raw_text": f"Synthetic smoke evidence {trx_id}",
                            "trx_id": trx_id,
                            "amount_minor": payable_minor,
                            "currency": "USD",
                            "remark": remark,
                        },
                    )
                )
            if evidence.get("state") not in {"RECEIVED", "ALLOCATED"}:
                raise RuntimeError(
                    f"synthetic evidence entered unexpected state {evidence.get('state')}"
                )

            paid = wait_for_paid(client, intent_id, timeout)

        webhook = capture.wait_for_paid(intent_id, timeout)
        payload = webhook["payload"]
        data = payload["data"]
        if int(paid["paid_minor"]) != payable_minor:
            raise RuntimeError("paid amount does not equal fingerprinted payable amount")
        if int(paid["excess_minor"]) != 0:
            raise RuntimeError("fingerprint offset was incorrectly counted as excess")
        if data.get("trx_id") != trx_id:
            raise RuntimeError("paid webhook transaction ID does not match evidence")

        return {
            "business_id": onboarded["business_id"],
            "source_id": source_id,
            "intent_id": intent_id,
            "evidence_id": evidence["id"],
            "webhook_event_id": webhook["event_id"],
            "status": paid["status"],
            "payable_amount_minor": payable_minor,
            "webhook_signature_valid": True,
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the no-money KHQR client integration smoke"
    )
    parser.add_argument(
        "--api-url",
        default=os.getenv("KHQR_API_URL", DEFAULT_API_URL),
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    try:
        result = run_smoke(args.api_url.rstrip("/"), args.timeout)
    except Exception as exc:
        print(f"KHQR CLIENT INTEGRATION SMOKE FAIL: {type(exc).__name__}: {exc}")
        return 1

    print("KHQR CLIENT INTEGRATION SMOKE PASS")
    print(f"- business_id={result['business_id']}")
    print(f"- source_id={result['source_id']}")
    print(f"- intent_id={result['intent_id']}")
    print(f"- evidence_id={result['evidence_id']}")
    print(f"- status={result['status']}")
    print(f"- payable_amount_minor={result['payable_amount_minor']}")
    print(f"- webhook_event_id={result['webhook_event_id']}")
    print("- webhook_signature_valid=true")
    print("- telegram_shadow_only=true")
    print("- live_telegram_enabled=false")
    print("- shadow_promotion_enabled=false")
    print("- secrets=redacted-from-stdout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
