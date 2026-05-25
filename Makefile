.PHONY: dev dev-d stop clean migrate migrate-down migrate-gen migrate-status migrate-history worker beat test test-local logs logs-api logs-worker shell bash lint fmt

#  Infrastructure 
dev:
	docker compose up --build

dev-d:
	docker compose up --build -d

stop:
	docker compose down

clean:
	docker compose down -v --remove-orphans

migrate:
	docker compose exec api alembic upgrade head

migrate-down:
	docker compose exec api alembic downgrade -1

# Equivalent of: typeorm:generate migrations/name
# Usage: make migrate-gen msg="add_user_preferences"
migrate-gen:
	docker compose exec api alembic revision --autogenerate -m "$(msg)"

migrate-status:
	docker compose exec api alembic current

migrate-history:
	docker compose exec api alembic history --verbose

# Workers
worker:
	celery -A app.workers.celery_app worker --loglevel=info --concurrency=4

beat:
	celery -A app.workers.celery_app beat --loglevel=info

# Testing
test:
	docker compose exec api pytest tests/ -v

test-local:
	pytest tests/ -v

# Observability
logs:
	docker compose logs -f

logs-api:
	docker compose logs -f api

logs-worker:
	docker compose logs -f worker

# Dev utilities
# Equivalent of: docker exec -it backend bash
bash:
	docker exec -it backend bash

shell:
	docker compose exec api python

lint:
	ruff check app/ tests/
	mypy app/

fmt:
	ruff format app/ tests/
