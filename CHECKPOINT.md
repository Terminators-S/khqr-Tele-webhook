# Checkpoint — 2026-09-19

## Locked decisions
- Checkout TTL: 8 minutes.
- Late automatic match grace: 5 minutes.
- Reservation lifetime: 13 minutes.
- Offset range: 0..29 cents.
- Overflow: REMARK_PRIMARY; checkout must not fail merely because all cent slots are busy.
- Historical recovery default: 7 days using exact Remark and/or Trx ID.
- Amount-only matching after reservation expiry is forbidden.
- Partial and multiple payments are supported; excess is tracked explicitly.
- Conflicting evidence fails closed.
- Webhooks are at-least-once with stable event IDs.
- PostgreSQL row locks/SKIP LOCKED own concurrency.
- Telegram defaults to SHADOW evidence.
- Live Telegram and SHADOW promotion each require a separate explicit enable flag.
- CreativeStudioWeb remains production payment/fulfillment authority until a later approved cutover.

## Verified
- PostgreSQL migration + Alembic drift gate: PASS.
- Focused PostgreSQL suite: 29/29 PASS.
- 100-way same-price allocation: 30 DUAL + 70 REMARK_PRIMARY.
- Duplicate settlement: exactly one allocation.
- 8 SKIP LOCKED workers: 40/40 rows exactly once.
- Docker/Compose API + settlement + webhook path: PASS.
- HTTP intent -> ABA evidence -> worker -> PAID: PASS.
- Shadow evidence stays PENDING while worker runs; explicit promotion then settles PAID: PASS.
- Real Creative Studio ABA journal parse: 35/35.
- Live Creative Studio PostgreSQL Trx/amount parity: 35/35.
- API ID/hash copied into git-ignored standalone .env without disclosure.
- Non-zero cent offset is accounted as required payable amount, not excess: PASS.
- Local RC gate from empty PostgreSQL volume: PASS.
- Python wheel contains both server package and khqr_sdk: PASS.
- Fresh loopback core deployment (API + PostgreSQL + settlement + webhook): healthy with clean database.
- Long-running Telegram preflight intentionally remains blocked while the dedicated session is missing and the staged source is disabled.
- Reusable client onboarding command added; credentials are owner-only (0600) and secrets stay out of stdout.
- No-money integration smoke PASS through the running API, settlement worker, and webhook worker: SDK intent -> synthetic RECEIVED evidence -> PAID -> verified signed webhook.
- Client onboarding supports staged disabled sources when the trusted Telegram sender is not yet known.
- Guarded source activation API added: sender can change only while disabled; enabling fails closed unless source configuration is complete.
- Focused onboarding/API/SDK/webhook suite: 15/15 PASS on isolated PostgreSQL.
- Full isolated PostgreSQL suite after activation slice: 36/36 PASS.
- PostgreSQL concurrency gates after activation slice: 30 DUAL + 70 overflow, one duplicate-settlement winner, 40/40 SKIP LOCKED exactly once.
- Fresh Docker image build/import PASS after the client-integration slice.
- Core redeployed healthy on loopback after destructive gate cleanup.
- First consuming project staged: Creative Studio Business + ABA source exists in standalone with real source identity, source disabled, sender unset, 0 intents, 0 evidence. CreativeStudioWeb remains authoritative.
- Staged Creative Studio credentials are stored only under git-ignored runtime/ with mode 0600.
- Read-only Telegram sender-discovery probe added for staged disabled sources; it cannot ingest evidence, mutate source state, enable payments, or promote SHADOW evidence.
- Sender-discovery focused tests: 5/5 PASS on isolated PostgreSQL.
- Missing-session fail-closed proof: discovery blocked with 0 intents and 0 evidence before/after.
- Pyrogram session path construction fixed to use separate client name/workdir; host and Docker absolute paths resolve to the same session filename contract.
- Session bootstrap now enforces both cutover fuses off, runtime directory 0700, session file 0600, and re-validates authorization instead of trusting file existence.
- Bootstrap reached the real Telegram phone prompt; the resulting partial file was verified unauthorized and removed.
- One-shot staged SHADOW observer added; it requires the source to remain disabled, requires configured trusted sender, writes SHADOW only, and never promotes/settles.
- Telegram migration focused tests (session path + sender discovery + SHADOW observer): 12/12 PASS.
- Full isolated PostgreSQL suite after SHADOW-observer slice: 48/48 PASS.
- PostgreSQL concurrency gates after SHADOW-observer slice: 30 DUAL + 70 overflow, one duplicate-settlement winner, 40/40 SKIP LOCKED exactly once.
- Dedicated Telegram session is now authorized at runtime/khqr_collector.session with mode 0600; Creative Studio's active admin_session.session was not reused.
- Fresh-session peer-cache warmup added before numeric group history reads so valid configured group IDs can resolve in a newly authorized Pyrogram session without exposing dialog contents.
- Read-only sender discovery PASS on the staged Creative Studio source: 3/3 parsed ABA notifications came from one unanimous trusted sender; database writes remained zero.
- Trusted sender bound through the guarded internal endpoint while the source remained disabled/not-ready.
- One-shot SHADOW replay PASS: 11 visible history messages, 3 payment notifications, 3 SHADOW evidence rows, 0 sender mismatches, 0 conflicts, 0 non-SHADOW evidence.
- Idempotent SHADOW replay PASS: second replay produced 3 existing rows, 0 new rows, 0 conflicts.
- Creative Studio parity PASS for all 3 available payments: 3/3 Trx ID match, 3/3 amount match, 3/3 timestamps within 5 seconds, 3/3 source-group match, max time delta 0.000 seconds.
- Dedicated account currently exposes only 11 history messages in that group, so 3 payments are the full visible parity sample at this checkpoint.
- Full isolated PostgreSQL suite after peer-cache warmup: 48/48 PASS.
- Full isolated PostgreSQL suite after SHADOW timestamp/parity artifact update: 48/48 PASS.
- Telegram API credentials are configured; TELEGRAM_SHADOW_ONLY=true, ALLOW_LIVE_TELEGRAM=false, ALLOW_SHADOW_PROMOTION=false.

## Next task
Keep Creative Studio authoritative and the standalone source disabled. Expand SHADOW parity when more payment notifications become visible/arrive, then prepare the explicit cutover runbook and rollback gate. Do not enable the source, ALLOW_SHADOW_PROMOTION, or ALLOW_LIVE_TELEGRAM without a separate approved cutover decision.
