.PHONY: dev stop status logs verify-setup verify-config verify-restart

dev:
	docker compose up --build --wait --wait-timeout 180

stop:
	docker compose down

status:
	docker compose ps

logs:
	docker compose logs --tail 100

verify-config:
	python3 -m unittest discover -s tests -p 'test_setup.py' -v

verify-setup: verify-config
	python3 tests/smoke_setup.py

verify-restart: verify-config
	python3 tests/smoke_setup.py --restart

.PHONY: verify-durability
verify-durability:
	docker compose exec -T api python -m unittest discover -s tests -p test_durability.py -v
	python3 tests/smoke_durability.py
	python3 tests/smoke_worker_health.py

.PHONY: verify-fixtures verify-security
verify-fixtures:
	docker compose exec -T api python -m unittest discover -s tests -p test_github_fixtures.py -v
	docker compose exec -T api python -m unittest discover -s tests -p test_verification.py -v

verify-security:
	python3 tools/verify_security.py --prepare
	python3 -m unittest discover -s tests -p test_security_supervisor.py -v
	docker build --target verifier -t connector-studio-verifier -f backend/Dockerfile .
	python3 tools/verify_security.py
