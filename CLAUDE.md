# Games Repository Conventions

## Structure

- `frontend/` is the Angular 20 SSR application.
- `backend/` is the FastAPI application, managed with `uv`.
- `backend/src/application/` contains application configuration, contracts, and the IoC container.
- `backend/src/infrastructure/` contains integrations separated by service (`database`, `redis`, `opensearch`, `rabbitmq`, and `s3`).
- `backend/src/features/` contains feature-specific controllers, services, repositories, and models.
- `backend/src/shared/` contains cross-cutting middleware, request/response helpers, utilities, and safe exception types.

Keep feature code out of `shared` and infrastructure-specific code out of `application`.

## Backend conventions

- Use async APIs for database, cache, search, messaging, and S3 work.
- Add type annotations to all production and test code. Run `uv run mypy` from `backend/`; strict mypy must remain clean.
- Register services in `build_container` in `backend/src/main.py`.
  - Use singleton for immutable settings and long-lived infrastructure clients.
  - Use scoped for request-owned objects such as `AsyncSession`.
  - Use transient for stateless, short-lived services.
- Resolve request dependencies through the active IoC scope; do not instantiate infrastructure clients directly in routes or features.
- Add Alembic migrations for schema changes. Do not use `Base.metadata.create_all` in application startup.

## HTTP and error conventions

- Preserve the middleware stack: CORS, request-scoped IoC, request ID/timing logs, security headers, and exception translation.
- Raise exceptions from `src.shared.exceptions` for expected client-facing failures. Add a subclass there when a new status/code/message mapping is needed.
- API errors use `{ "error": { "code": "...", "message": "..." } }`.
- Never return exception details, stack traces, credentials, or internal service errors to clients. Unknown errors must become the generic 500 response and be logged internally.

## Local workflow

1. Copy root `.env.example` to `.env`; never commit `.env` or real AWS credentials.
2. Run the full stack with `docker compose up --build` from the repository root.
3. Defaults: frontend `http://localhost:3040`, backend `http://127.0.0.1:8040`. Container-to-container API traffic uses `http://api:8000`.
4. Use `cd backend && uv run pytest` for fast tests, `uv run pytest -m integration` only after Compose is running, and `uv run mypy` before handoff.
5. Use `cd frontend && npm run build` to validate Angular changes.

## Compose and configuration

- Host ports are configurable via `FRONTEND_PORT` and `BACKEND_PORT`; do not change container ports without a reason.
- Keep new configuration environment-driven and document defaults in root `.env.example`.
- AWS S3 is external: never add an S3 emulator unless requirements change.
