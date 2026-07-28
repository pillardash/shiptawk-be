from urllib.parse import urlsplit

from app.shared.exceptions import BadRequestError


def validate_return_path(return_path: str) -> str:
    parsed = urlsplit(return_path)
    if (
        not return_path.startswith("/")
        or return_path.startswith("//")
        or "\\" in return_path
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
    ):
        raise BadRequestError("Invalid return path.", code="invalid_return_path")
    return return_path
