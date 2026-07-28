from ipaddress import ip_address
from urllib.parse import parse_qsl, urlsplit


def _is_private_hostname(hostname: str) -> bool:
    normalized = hostname.lower().rstrip(".")
    if normalized == "localhost" or normalized.endswith(".local"):
        return True
    try:
        address = ip_address(normalized)
    except ValueError:
        return False
    return not address.is_global


def is_safe_public_source_url(value: str) -> bool:
    parsed = urlsplit(value)
    return not (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or _is_private_hostname(parsed.hostname)
        or any(
            key.lower() in {"access_token", "api_key", "key", "password", "secret", "token"}
            for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
        )
    )
