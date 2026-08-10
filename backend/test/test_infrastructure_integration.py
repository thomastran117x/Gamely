import pytest
from sqlalchemy import text

from src.application.environment.environment_manager import Settings
from src.infrastructure.services import InfrastructureServices
from src.main import create_app


@pytest.mark.integration
async def test_container_dependencies_are_reachable(
    integration_settings: Settings,
) -> None:
    services = InfrastructureServices(integration_settings)
    await services.connect()
    try:
        async with services.engine.connect() as connection:
            assert (await connection.execute(text("SELECT 1"))).scalar_one() == 1
        assert await services.redis.ping() is True
        assert await services.opensearch.ping() is True
        await services.ready()
    finally:
        await services.close()


@pytest.mark.integration
def test_readiness_against_container_dependencies(
    integration_settings: Settings,
) -> None:
    from fastapi.testclient import TestClient

    with TestClient(create_app(integration_settings)) as client:
        assert client.get("/health/ready").status_code == 200
