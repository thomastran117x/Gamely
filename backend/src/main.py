from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import RequestResponseEndpoint

from src.application.contracts import ApplicationServices
from src.application.environment.environment_manager import Settings
from src.application.ioc import ServiceCollection, ServiceProvider, ServiceScope
from src.features.auth.auth_controller import AuthController, router as auth_router
from src.features.auth.auth_repository import AuthRepository
from src.features.auth.auth_service import AuthService
from src.features.auth.oauth.oauth_service import OAuthService
from src.features.auth.token.token_service import TokenService
from src.infrastructure.email import EmailPublisher
from src.infrastructure.services import InfrastructureServices
from src.shared.middlewares import ExceptionHandlingMiddleware, HttpLoggingMiddleware, SecurityHeadersMiddleware, register_http_exception_handlers


def build_container(settings: Settings, services_factory: Callable[[Settings], ApplicationServices]) -> ServiceProvider:
    services = ServiceCollection()
    services.add_instance(Settings, settings)
    services.add_singleton(ApplicationServices, lambda resolver: services_factory(resolver.get(Settings)))
    services.add_scoped(AsyncSession, lambda resolver: cast(InfrastructureServices, resolver.get(ApplicationServices)).session_factory())
    services.add_scoped(AuthRepository, lambda resolver: AuthRepository(resolver.get(AsyncSession)))
    services.add_transient(TokenService, lambda resolver: TokenService(cast(InfrastructureServices, resolver.get(ApplicationServices)).redis, resolver.get(Settings)))
    services.add_transient(EmailPublisher, lambda resolver: EmailPublisher(cast(InfrastructureServices, resolver.get(ApplicationServices)).rabbitmq))
    services.add_scoped(AuthService, lambda resolver: AuthService(resolver.get(AuthRepository), resolver.get(TokenService), cast(InfrastructureServices, resolver.get(ApplicationServices)).redis, resolver.get(EmailPublisher), resolver.get(Settings)))
    services.add_scoped(OAuthService, lambda resolver: OAuthService(resolver.get(AuthRepository), resolver.get(TokenService), cast(InfrastructureServices, resolver.get(ApplicationServices)).redis, resolver.get(Settings)))
    services.add_scoped(AuthController, lambda resolver: AuthController(resolver.get(AuthService), resolver.get(OAuthService), resolver.get(TokenService), resolver.get(Settings)))
    return services.build_provider()


async def get_services(request: Request) -> ApplicationServices:
    return cast(ServiceScope, request.state.service_scope).get(ApplicationServices)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    yield cast(ServiceScope, request.state.service_scope).get(AsyncSession)


def create_app(settings: Settings | None = None, services_factory: Callable[[Settings], ApplicationServices] = InfrastructureServices) -> FastAPI:
    app_settings = settings or Settings()  # type: ignore[call-arg]
    container = build_container(app_settings, services_factory)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = container.get(ApplicationServices)
        app.state.container = container
        await services.connect()
        try:
            yield
        finally:
            await services.close()

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.state.settings = app_settings
    app.include_router(auth_router)
    register_http_exception_handlers(app)
    app.add_middleware(ExceptionHandlingMiddleware)
    app.add_middleware(HttpLoggingMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CORSMiddleware, allow_origins=app_settings.cors_origin_list, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def request_scope(request: Request, call_next: RequestResponseEndpoint) -> Response:
        async with container.create_scope() as scope:
            request.state.service_scope = scope
            return await call_next(request)

    @app.get("/")
    async def root() -> dict[str, str]: return {"name": app_settings.app_name, "status": "ok"}

    @app.get("/health/live")
    async def liveness() -> dict[str, str]: return {"status": "ok"}

    @app.get("/health/ready")
    async def readiness(request: Request) -> JSONResponse:
        try: await (await get_services(request)).ready()
        except Exception: return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "unavailable"})
        return JSONResponse(content={"status": "ok"})

    return app


app = create_app()
