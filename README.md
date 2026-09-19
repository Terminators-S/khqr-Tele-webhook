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
3. Create a Business; save its one-time API key/webhook secret.
4. Create a disabled PaymentSource with merchant alias + static KHQR.
5. Configure Telegram API credentials, authorize the dedicated Telegram account, load the merchant group, and discover the trusted ABA sender.
6. Open **Real Test**, choose a small USD amount, and type `SEND REAL TEST MONEY`.
7. Scan the merchant KHQR, enter the exact generated amount and Remark, and watch the dashboard verify the real ABA/Telegram notification.
8. Real Test evidence stays SHADOW-only: no allocation, webhook, credit, or product fulfillment is allowed.
9. Dashboard activation remains blocked until at least one Real Test is verified.
10. Migration/cutover operators should still run SHADOW parity + `make cutover-preflight` before any separate production cutover.

## Real payment acceptance
The dashboard acceptance harness uses the production Remark/cent-fingerprint matching rules while keeping the normal source disabled. It creates a dashboard-only test request, watches only the configured Telegram group/trusted sender, ingests the observed transaction as SHADOW evidence, and reports the status the settlement engine would produce. It never promotes the evidence, allocates it, sends a webhook, or fulfills a product.

See `docs/CLIENT_ONBOARDING.md` for client setup and the no-money smoke, and `docs/IMPORT_FROM_CREATIVE_STUDIO.md` for the extraction boundary.
