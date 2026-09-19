# KHQR Self-Develop

Standalone, reusable KHQR/ABA payment verification service for Creative Studio and other projects.

The service issues a payment request with a short Remark plus an independent cent fingerprint, persists ABA/PayWay Telegram evidence, settles payments atomically, and notifies client applications through signed webhooks. Product/order fulfillment belongs to the client application.

## Payment identity
- Base amount is stored in integer cents.
- Preferred offset tier: $0.00-$0.09.
- Extended tier: $0.10-$0.29.
- 30 cent slots are attempted before safe overflow to REMARK_PRIMARY.
- 8-minute buyer checkout TTL + 5-minute late-match grace = 13-minute reservation lifetime.
- Exact Remark can recover a payment after the amount fingerprint has been recycled.
- Trx ID is globally immutable inside a payment source and can be allocated once.

## Runtime
Production is four separable processes sharing PostgreSQL: API, settlement worker, webhook worker, and Telegram collector. One Telegram user session may watch multiple business groups; each group maps to one PaymentSource.

## Quick start
1. Run `./scripts/install.sh` on Linux/macOS or `./scripts/install.ps1` on Windows.
2. Open `http://127.0.0.1:8088/dashboard` and sign in with the generated `INTERNAL_SECRET`.
3. In **Setup → Store**, create or select a store. Each store owns an isolated payment source and may use its own currency, webhook, KHQR image and Telegram payment group.
4. In **Setup → Upload your KHQR**, upload the exact reusable static KHQR image issued by the merchant's bank/Bakong. The service decodes and validates the QR payload and CRC, stores the original image bytes unchanged, and serves that same uploaded image to customers/tests. It does not generate a replacement merchant QR.
5. In **Setup → Telegram**, save Telegram API ID/hash, authorize the dedicated Telegram account, load the merchant payment-notification group, then select it. The dashboard discovers and binds the trusted ABA notification sender automatically for the selected store.
6. Open **Integration**. Add the real website/app/bot webhook URL, save the one-time signing secret, and copy the project/store ID, payment-source ID and API endpoint. If the API key shown at store creation was lost, rotate it here.
7. From the consuming application's backend, create `/v1/payment-intents` with `X-Api-Key`, `Idempotency-Key`, the source ID, the application's own order/product `external_id`, integer minor-unit amount and optional metadata. Product/catalog authority stays in the consuming application.
8. Open **Test payment**, choose a small USD amount, and type `SEND REAL TEST MONEY`.
9. Scan the uploaded merchant KHQR, enter the exact generated amount and Remark, and watch the dashboard verify the real ABA/Telegram notification.
10. Real Test evidence stays SHADOW-only: no allocation, webhook, credit, or product fulfillment is allowed.
11. Dashboard activation remains blocked until the selected store has an uploaded verified KHQR image, configured signed project webhook, a verified Real Payment Test, and the explicit live Telegram cutover flags (`TELEGRAM_SHADOW_ONLY=false`, `ALLOW_LIVE_TELEGRAM=true`).
12. After those flags are applied and core services restarted, enable the source in **Integration**, then start the live collector with `docker compose --profile telegram up -d telegram`.
13. Migration/cutover operators should still run SHADOW parity + `make cutover-preflight` before any separate production cutover.

## Real payment acceptance
The dashboard acceptance harness uses the production Remark/cent-fingerprint matching rules while keeping the normal source disabled. It creates a dashboard-only test request, watches only the configured Telegram group/trusted sender, ingests the observed transaction as SHADOW evidence, and reports the status the settlement engine would produce. It never promotes the evidence, allocates it, sends a webhook, or fulfills a product.

See `docs/CLIENT_ONBOARDING.md` for client setup and the no-money smoke, and `docs/IMPORT_FROM_CREATIVE_STUDIO.md` for the extraction boundary.
## Safe testing
Run `make test`. Tests use a physically separate PostgreSQL instance at `127.0.0.1:55433/khqr_test` under the `khqr-self-develop-test` Compose project. The pytest bootstrap refuses the runtime database/port, and the test container/volume are removed after the run. `make rc-gate` uses the same isolated test project; it never runs `down -v` against the runtime Compose project.
