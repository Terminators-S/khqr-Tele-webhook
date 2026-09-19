.PHONY: test rc-gate deploy-core stop-core integration-smoke shadow-preflight

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

shadow-preflight:
	.venv/bin/python scripts/telegram_shadow_preflight.py
