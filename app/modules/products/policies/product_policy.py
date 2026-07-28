from ipaddress import ip_address
from urllib.parse import urlsplit


def validate_public_website_url(value: str) -> str:
    if not value:
        return value
    parsed = urlsplit(value)
    hostname = parsed.hostname
    if hostname is None:
        raise ValueError("websiteUrl must be a public HTTP(S) URL")
    normalized = hostname.lower().rstrip(".")
    try:
        address_is_private = not ip_address(normalized).is_global
    except ValueError:
        address_is_private = normalized == "localhost" or normalized.endswith(".local")
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or address_is_private
    ):
        raise ValueError("websiteUrl must be a public HTTP(S) URL")
    return value
