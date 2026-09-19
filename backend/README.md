# Games backend

The backend is an asynchronous FastAPI application managed with `uv`. It currently implements
authentication, account recovery, OAuth token verification, health endpoints, infrastructure
clients, and an independent RabbitMQ email worker.

See the root [README](../README.md) for the full-stack quick start and
[ARCHITECTURE.md](../ARCHITECTURE.md) for component boundaries and runtime flows.

## Prerequisites

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Docker for infrastructure and integration tests

Install locked application and development dependencies:

```bash
uv sync --all-groups --frozen
```

## Configuration

Copy the repository-root `.env.example` to `.env` and set `AUTH_JWT_SECRET` to a random value of at
least 32 characters. Never commit `.env` or real credentials.

Non-secret backend defaults live in `config/default.yml`; `config/dev.yml`, `config/test.yml`, and
`config/prod.yml` provide stage overrides selected by `APP_ENV`. See
[ARCHITECTURE.md](../ARCHITECTURE.md#configuration) for precedence and interpolation behavior.

The connection URLs in `.env.example` use Docker service names. For a backend process running
directly on the host, change those hosts to `127.0.0.1` while keeping the published ports, for
example `postgresql+asyncpg://games:games@127.0.0.1:5432/games`.

## Running the API

Start PostgreSQL, Redis, OpenSearch, and RabbitMQ through Compose or equivalent services, then run:

```bash
uv run alembic upgrade head
uv run uvicorn src.main:app --reload
```

The native API defaults to <http://127.0.0.1:8000>. OpenAPI documentation is at `/docs`, liveness is
at `/health/live`, and dependency readiness is at `/health/ready`.

To run the complete containerized stack from the repository root instead:

```bash
docker compose up --build
```

Compose publishes the API at <http://127.0.0.1:8040> by default.

## Database migrations

Apply committed migrations before running code that depends on them:

```bash
uv run alembic upgrade head
```

After changing SQLAlchemy models, create and review a migration:

```bash
uv run alembic revision --autogenerate -m "describe change"
```

Never use `Base.metadata.create_all` during application startup.

## Dependency injection

Register dependencies in `build_container` in `src/main.py`. Immutable settings and long-lived
infrastructure clients are singletons, request-owned sessions and feature services are scoped, and
stateless short-lived helpers are transient. Routes must resolve dependencies through the active
request scope.

## Validation

Run formatting, strict typing, and fast tests from this directory:

```bash
uv run ruff format --check
uv run mypy
uv run pytest
```

The default pytest configuration excludes tests marked `integration`. Integration tests require a
running Docker engine; Testcontainers provisions PostgreSQL, Redis, OpenSearch, and RabbitMQ for
each relevant module and removes them afterward:

```bash
uv run pytest -m integration
```

AWS S3 is intentionally not emulated or readiness-checked. Configure valid AWS credentials and
`S3_BUCKET` only when exercising an S3-backed feature.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for change, changelog, and pull request requirements.
