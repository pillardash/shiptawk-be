from app.shared.schemas import ApiSchema


class ErrorResponse(ApiSchema):
    detail: str
    code: str
    request_id: str


class ValidationErrorItem(ApiSchema):
    field: str
    message: str
    type: str


class ValidationErrorResponse(ErrorResponse):
    errors: list[ValidationErrorItem]


class HealthResponse(ApiSchema):
    status: str
    app: str
    environment: str
    version: str


class ReadinessResponse(ApiSchema):
    status: str


class MessageResponse(ApiSchema):
    message: str
