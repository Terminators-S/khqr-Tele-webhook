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
1. Copy .env.example to .env.
2. Start PostgreSQL.
3. Run Alembic migrations.
4. Start the API, settlement and webhook workers.
5. Create a Business and PaymentSource through internal-admin endpoints.
6. Give the returned API key to the client project; it is shown only once.
7. Run `scripts/integration_smoke.py` to prove SDK intent → synthetic evidence → settlement → signed webhook without real money.
8. For Telegram observation, create a dedicated session and run `scripts/telegram_shadow_preflight.py` before starting the `telegram` profile. Shadow mode and both cutover fuses are safe by default.

See `docs/CLIENT_ONBOARDING.md` for client setup and the no-money smoke, and `docs/IMPORT_FROM_CREATIVE_STUDIO.md` for the extraction boundary.
