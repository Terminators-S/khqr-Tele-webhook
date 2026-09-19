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

## Dashboard-first open-source setup

For a normal cloned installation, use the dashboard instead of the internal CLI:

1. **Setup → Store:** create the merchant/project identity and choose currency.
2. **Setup → Upload your KHQR:** upload the bank-issued reusable static KHQR image. The service validates and keeps that exact image; it does not generate a replacement merchant QR.
3. **Setup → Telegram:** enter the Telegram API ID/hash, authorize the dedicated account, load groups, select the store's ABA notification group, and let sender discovery bind the trusted notification sender.
4. **Integration:** configure the consuming website/app/bot webhook, save the one-time signing secret, and save or rotate the one-time client API key. The dashboard shows the project ID, payment-source ID, API URL, Telegram mapping, and a copyable payment-intent request.
5. **Test payment:** run the SHADOW-only real-money acceptance test while the source is still disabled.
6. **Go live:** keep `TELEGRAM_SHADOW_ONLY=true` and `ALLOW_LIVE_TELEGRAM=false` until the project webhook, KHQR, Telegram group/sender and Real Payment Test are all green. At approved cutover set `TELEGRAM_SHADOW_ONLY=false` and `ALLOW_LIVE_TELEGRAM=true`, restart core services, enable the source in the dashboard, then start the Telegram profile with `docker compose --profile telegram up -d telegram`.

The merchant application remains the authority for products, prices, orders and fulfillment. KHQR Self-Develop receives a stable external order/product ID plus integer minor-unit amount, verifies payment evidence, then emits a signed payment event back to the merchant application.

## Advanced CLI onboarding

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

### 1. Read-only sender discovery

Create the dedicated standalone Telegram session with
`scripts/create_telegram_shadow_session.py`. The session file must live directly
under `runtime/`, be mode `0600`, and must not reuse Creative Studio's active
session file.

Discover the notification sender without writing payment evidence:

```bash
make sender-discovery SOURCE_ID="<staged-source-id>"
```

The probe reads recent group history, parses ABA payment notifications, and
requires one unanimous sender identity. It writes only an owner-only JSON
observation artifact under `runtime/`; it does not ingest evidence, create
intents, alter source configuration, or activate payments.

### 2. Configure the observed sender while still disabled

After reviewing the discovery artifact, bind the observed sender:

```text
POST /internal/sources/{source_id}/sender
{"telegram_sender_id": 123456789}
```

Do not enable the source yet.

### 3. One-shot SHADOW parity observation

Replay a bounded history into SHADOW evidence while the source remains disabled:

```bash
make shadow-observe SOURCE_ID="<staged-source-id>"
```

This gate accepts only the configured sender, ignores unrelated/unparseable
messages, writes matched notifications as `SHADOW`, never promotes or settles
them, and exits after the bounded replay.

Regenerate the incumbent parity artifact with the read-only Creative Studio
migration checker:

```bash
make creative-parity
```

It queries only matching transaction IDs from Creative Studio, emits no raw
payment contents, and writes aggregate Trx/amount/time/source parity to
`runtime/creative-studio-parity.json` with mode `0600`.

### 4. Pass the fail-closed cutover preflight

Before any activation, run:

```bash
make cutover-preflight SOURCE_ID="<staged-source-id>"
```

The default gate requires at least 10 SHADOW payments plus a passing
`runtime/creative-studio-parity.json`. It also verifies that the dedicated
session is owner-only, both live cutover fuses remain off, the source is still
disabled but fully configured, all observed evidence remains SHADOW, and no
payment allocations exist. The command never changes source state or fuses.

For a stricter release gate, increase the threshold explicitly:

```bash
make cutover-preflight SOURCE_ID="<staged-source-id>" MIN_SHADOW=20
```

### 5. Enable only at an approved cutover

Only after sender discovery, SHADOW parity, and the cutover preflight are all
accepted should activation be considered:

```text
POST /internal/sources/{source_id}/enabled
{"enabled": true}
```

Enabling fails closed if group, sender, merchant alias, or static KHQR is
incomplete. Sender changes are rejected while a source is enabled; disable it
first before correcting the sender. Rollback remains the explicit
`enabled=false` operation.

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
