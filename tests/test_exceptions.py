from fastapi import APIRouter, Query
from fastapi.testclient import TestClient

from app.main import create_app
from app.shared.exceptions import NotFoundError


def test_app_error_response_includes_request_id() -> None:
    app = create_app()
    router = APIRouter()

    @router.get("/boom")
    def boom() -> None:
        raise NotFoundError("Missing resource.")

    app.include_router(router)
    client = TestClient(app)

    response = client.get("/boom", headers={"X-Request-ID": "request-456"})

    assert response.status_code == 404
    assert response.json() == {
        "detail": "Missing resource.",
        "code": "not_found",
        "requestId": "request-456",
    }


def test_validation_error_response_uses_camel_case() -> None:
    app = create_app()
    router = APIRouter()

    @router.get("/validate")
    def validate(page_size: int = Query(ge=1)) -> dict[str, int]:
        return {"pageSize": page_size}

    app.include_router(router)
    client = TestClient(app)

    response = client.get("/validate?page_size=0", headers={"X-Request-ID": "request-789"})

    assert response.status_code == 422
    assert response.json() == {
        "detail": "Request validation failed.",
        "code": "validation_error",
        "requestId": "request-789",
        "errors": [
            {
                "field": "pageSize",
                "message": "Input should be greater than or equal to 1",
                "type": "greater_than_equal",
            }
        ],
    }
