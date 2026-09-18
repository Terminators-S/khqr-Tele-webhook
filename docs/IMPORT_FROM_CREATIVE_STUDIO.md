# Extraction Boundary

CreativeStudioWeb remains the current production commerce/payment authority. This repository does not own product inventory, order fulfillment, reseller sourcing, coupons, credits, customer accounts, or storefront state.

Imported concepts:
- KHQR/static-QR presentation data
- ABA Telegram evidence parsing and history reconciliation
- payment-intent identity
- cent-offset collision avoidance
- immutable transaction IDs
- late-payment matching windows

Rebuilt here rather than copied:
- money uses integer minor units
- PostgreSQL owns concurrency
- amount reservations are explicit rows with unique constraints
- evidence, allocation, claims, events and webhook outbox are separate durable records
- settlement is worker-owned and idempotent
- client fulfillment is notified by signed webhook rather than called directly

Creative Studio protected production code must not be changed until a later integration gate explicitly approves shadow parity and rollback.
