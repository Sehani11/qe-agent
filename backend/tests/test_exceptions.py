"""Tests for global exception handlers."""

from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.main import app

# Add an inline router/endpoint to trigger errors for testing
test_router = APIRouter()

@test_router.get("/error/http")
async def trigger_http_error() -> dict:
    from fastapi import HTTPException
    raise HTTPException(status_code=400, detail="Custom HTTP error")

@test_router.get("/error/unhandled")
async def trigger_unhandled_error() -> dict:
    raise ValueError("Unhandled error")

# temporarily include it
app.include_router(test_router, prefix="/api/v1")

client = TestClient(app)

def test_http_exception_envelope() -> None:
    response = client.get("/api/v1/error/http")
    assert response.status_code == 400
    data = response.json()
    assert data["error"] == "HTTP_ERROR"
    assert data["message"] == "Custom HTTP error"
    assert data["code"] == 400

def test_global_exception_envelope() -> None:
    # Need to disable raise_server_exceptions logic for TestClient
    # but the simplest way is to test the actual endpoint.
    client_safe = TestClient(app, raise_server_exceptions=False)
    response = client_safe.get("/api/v1/error/unhandled")
    assert response.status_code == 500
    data = response.json()
    assert data["error"] == "INTERNAL_SERVER_ERROR"
    assert data["code"] == 500
    assert "Unhandled error" not in data["message"] # Should be masked

def test_validation_exception_envelope() -> None:
    # Since we are sending bad JSON directly to an endpoint that doesn't exist
    # or that doesn't accept the right type, it will 422.
    from pydantic import BaseModel

    class TestModel(BaseModel):
        num: int

    @app.post("/api/v1/error/validation")
    async def trigger_validation(data: TestModel) -> dict:
        return {"status": "ok"}

    # Reload local test client with new route
    client2 = TestClient(app)
    response = client2.post("/api/v1/error/validation", json={"num": "not-an-int"})

    assert response.status_code == 422
    data = response.json()
    assert data["error"] == "VALIDATION_ERROR"
    assert data["code"] == 422
    assert "details" in data
