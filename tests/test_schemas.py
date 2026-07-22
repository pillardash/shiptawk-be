from app.main import app
from app.shared.pagination import PageResponse, build_page_meta
from app.shared.responses import ErrorResponse
from app.shared.schemas import ApiSchema, to_camel


class ExampleSchema(ApiSchema):
    request_id: str


def test_to_camel_converts_snake_case() -> None:
    assert to_camel("request_id") == "requestId"
    assert to_camel("page_size") == "pageSize"


def test_api_schema_serializes_with_camel_case_aliases() -> None:
    schema = ExampleSchema(request_id="abc")

    assert schema.model_dump(by_alias=True) == {"requestId": "abc"}


def test_error_response_serializes_request_id_as_camel_case() -> None:
    response = ErrorResponse(detail="Missing", code="not_found", request_id="abc")

    assert response.model_dump(by_alias=True) == {
        "detail": "Missing",
        "code": "not_found",
        "requestId": "abc",
    }


def test_page_response_serializes_meta_as_camel_case() -> None:
    meta = build_page_meta(page=2, page_size=10, total_items=25)
    response = PageResponse[str](items=["item"], meta=meta)

    assert response.model_dump(by_alias=True) == {
        "items": ["item"],
        "meta": {
            "page": 2,
            "pageSize": 10,
            "totalItems": 25,
            "totalPages": 3,
            "hasNext": True,
            "hasPrevious": True,
        },
    }


def test_public_openapi_does_not_expose_restricted_raw_event_payload() -> None:
    openapi = str(app.openapi()).lower()

    assert "payloadciphertext" not in openapi
    assert "payloadkeyversion" not in openapi
    assert "githubrawevent" not in openapi
