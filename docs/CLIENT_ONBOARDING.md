# Client Onboarding and No-Money Smoke

KHQR Self-Develop keeps commerce and fulfillment in the consuming project.
A client receives an API key, source ID, and optional webhook secret, then uses
the Python SDK for payment intents and webhook verification.

## Safety boundary

- CreativeStudioWeb remains the live payment/fulfillment authority.
- Telegram remains shadow-only by default.
- `ALLOW_LIVE_TELEGRAM=false`.
- `ALLOW_SHADOW_PROMOTION=false`.
- The integration smoke injects synthetic evidence as `RECEIVED`; it does not
  use Telegram or promote SHADOW evidence.
- Secrets are written only to an owner-only file when requested and are never
  printed by the onboarding command.

## Onboard a consuming project

Provide the real source values through flags or environment variables:

```bash
.venv/bin/python scripts/onboard_consuming_project.py \
  --project-name "My Store" \
  --project-slug "my-store" \
  --merchant-alias "MY STORE" \
  --static-khqr "<static-khqr>" \
  --telegram-group-id "<group-id>" \
  --telegram-sender-id "<sender-id>" \
  --webhook-url "https://client.example/khqr/webhook" \
  --output-file runtime/my-store.env
```

The output file contains `KHQR_API_URL`, `KHQR_API_KEY`,
`KHQR_WEBHOOK_SECRET`, `KHQR_SOURCE_ID`, `KHQR_BUSINESS_ID`,
`KHQR_PROJECT_SLUG`, and `KHQR_CURRENCY`. Its mode is forced to `0600`.

### Stage a source before its Telegram sender is known

For a migration where the real group and KHQR identity are known but the trusted
Telegram sender has not yet been observed, create the source disabled:

```bash
.venv/bin/python scripts/onboard_consuming_project.py \
  --project-name "My Store" \
  --project-slug "my-store" \
  --merchant-alias "MY STORE" \
  --static-khqr "<static-khqr>" \
  --telegram-group-id "<group-id>" \
  --disabled \
  --output-file runtime/my-store.env
```

A staged source is intentionally not ready and cannot create payment intents.
After the dedicated standalone SHADOW collector proves the sender ID, configure
the sender while the source is still disabled:

```text
POST /internal/sources/{source_id}/sender
{"telegram_sender_id": 123456789}
```

Then enable it explicitly:

```text
POST /internal/sources/{source_id}/enabled
{"enabled": true}
```

Enabling fails closed if group, sender, merchant alias, or static KHQR is
incomplete. Sender changes are rejected while a source is enabled; disable it
first before correcting the sender. This gives rollback a single explicit
`enabled=false` operation.

### Read-only sender discovery

Create the dedicated standalone Telegram session with
`scripts/create_telegram_shadow_session.py`. The session file must live directly
under `runtime/`, be mode `0600`, and must not reuse Creative Studio's active
session file.

For a staged source, discover the notification sender without writing payment
evidence:

```bash
make sender-discovery SOURCE_ID="<staged-source-id>"
```

The probe reads recent group history, parses ABA payment notifications, and
requires one unanimous sender identity. It writes only an owner-only JSON
observation artifact under `runtime/`; it does not ingest evidence, create
intents, alter source configuration, or activate payments. Configure the sender
through the guarded internal endpoint only after reviewing the discovery output.

## Client intent contract

Use `khqr_sdk.KhqrClient.create_payment_intent()` with a stable external order
ID and idempotency key. Display the returned `payment_request.static_khqr`,
`payment_request.payable_amount_minor`, and exact `payment_request.remark`.

The buyer-facing checkout expires after 8 minutes. The amount reservation
continues for the 5-minute late-match grace, for 13 minutes total.

## Webhook contract

Verify `X-KHQR-Timestamp` and `X-KHQR-Signature` against the raw request body
before parsing or fulfilling anything. The SDK provides
`verify_webhook_signature()` and `decode_webhook()`. Fulfillment should only
run after a verified `payment.intent.paid` event and must be idempotent.
## No-money integration smoke

With the core Compose services already running:

```bash
.venv/bin/python scripts/integration_smoke.py --timeout 25
```

The smoke creates a unique throwaway business/source, creates an SDK intent,
injects unique synthetic evidence through `/internal/evidence`, waits for the
real settlement worker to reach `PAID`, and runs a temporary local HTTP receiver
reachable from the Docker webhook worker. The receiver verifies the actual
webhook HMAC and event ID.

The smoke intentionally leaves its throwaway database rows in place so evidence
can be inspected. Run the isolated RC gate afterward to destroy test data, then
redeploy the core:

```bash
make rc-gate
make deploy-core
```

Do not use the smoke as a substitute for later live Telegram SHADOW parity.
