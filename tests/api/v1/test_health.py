"""Integration tests for GET /api/v1/health."""

from httpx import AsyncClient


async def test_health_returns_200(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200


async def test_health_response_shape(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    body = response.json()
    assert body["success"] is True
    assert "timestamp" in body
    assert body.get("error") is None


async def test_health_data_fields(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    data = response.json().get("data", {})
    assert data.get("healthy") is True
    assert data.get("version") == "0.1.0"
    assert data.get("service") == "kodezart"


async def test_health_camel_case_keys(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    body = response.json()
    assert "success" in body
    assert "timestamp" in body
    assert "created_at" not in body


async def test_health_returns_valid_status(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["error"] is None
    assert body["data"] == {
        "healthy": True,
        "version": "0.1.0",
        "service": "kodezart",
    }
