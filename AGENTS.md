# Agent Instructions: Games

Read `claude.md` before changing this repository. It is the source of truth for architecture and workflow conventions.

When making changes:

- Keep the Angular frontend and FastAPI backend independently buildable.
- Preserve strict Python typing; run `cd backend && uv run mypy` and `uv run pytest` for backend changes.
- Add or update focused tests whenever behavior changes. Integration tests are marked `integration` and require Compose services.
- Use the IoC container in `backend/src/main.py` for new backend dependencies. Choose lifetimes deliberately: singleton, scoped, or transient.
- Use shared application exceptions and the established error JSON contract for expected HTTP failures. Do not expose stack traces or raw exception messages.
- Keep all secrets in the ignored root `.env`; update `.env.example` when adding configurable values.
- Keep Compose host ports dynamic. Defaults are frontend `3040` and backend `8040`; internal API communication remains `api:8000`.
- Avoid unrelated refactors and preserve existing user changes.

Before reporting completion, run the relevant build, type-check, test, and Compose-config commands and clearly state any validation that could not run.
