.PHONY: test rc-gate deploy-core stop-core integration-smoke sender-discovery shadow-observe shadow-preflight

test:
	.venv/bin/pytest -q

rc-gate:
	./scripts/rc_gate_local.sh

deploy-core:
	./scripts/deploy_core.sh

stop-core:
	docker compose stop api settlement webhook

integration-smoke:
	.venv/bin/python scripts/integration_smoke.py --timeout 25

sender-discovery:
	@test -n "$(SOURCE_ID)" || (echo "SOURCE_ID is required" >&2; exit 2)
	.venv/bin/python scripts/telegram_sender_discovery.py --source-id "$(SOURCE_ID)"

shadow-observe:
	@test -n "$(SOURCE_ID)" || (echo "SOURCE_ID is required" >&2; exit 2)
	.venv/bin/python scripts/telegram_shadow_observe.py --source-id "$(SOURCE_ID)"

shadow-preflight:
	.venv/bin/python scripts/telegram_shadow_preflight.py
