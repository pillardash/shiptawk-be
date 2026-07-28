import posixpath
import re
from ipaddress import ip_address
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

PUBLIC_URL_ERROR = "website URL must be a public HTTP(S) URL"
SAFE_PORTS = frozenset({80, 443})
_PRIVATE_HOST_SUFFIXES = (".internal", ".lan", ".local", ".localhost", ".home")
_HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _validated_parts(value: str) -> tuple[SplitResult, str, int | None]:
    if not value or value != value.strip() or any(ord(character) < 32 for character in value):
        raise ValueError(PUBLIC_URL_ERROR)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(PUBLIC_URL_ERROR) from exc

    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.hostname is None
        or port not in {None, *SAFE_PORTS}
        or "\\" in value
    ):
        raise ValueError(PUBLIC_URL_ERROR)

    raw_hostname = parsed.hostname.rstrip(".")
    try:
        hostname = raw_hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError(PUBLIC_URL_ERROR) from exc
    if not hostname or hostname == "localhost" or hostname.endswith(_PRIVATE_HOST_SUFFIXES):
        raise ValueError(PUBLIC_URL_ERROR)

    try:
        address = ip_address(hostname)
    except ValueError:
        labels = hostname.split(".")
        if len(labels) < 2 or any(not _HOST_LABEL.fullmatch(label) for label in labels):
            raise ValueError(PUBLIC_URL_ERROR) from None
    else:
        if not address.is_global:
            raise ValueError(PUBLIC_URL_ERROR)

    return parsed, hostname, port


def validate_public_website_url(value: str) -> str:
    """Validate request-independent URL safety without resolving DNS."""
    _validated_parts(value)
    return value


def normalize_website_url(value: str) -> str:
    """Return a stable page identity for a syntactically safe public URL."""
    stripped = value.strip()
    parsed, hostname, port = _validated_parts(stripped)
    scheme = parsed.scheme.lower()
    host_for_netloc = f"[{hostname}]" if ":" in hostname else hostname
    is_default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    if port is not None and not is_default_port:
        host_for_netloc = f"{host_for_netloc}:{port}"

    raw_path = re.sub(r"/{2,}", "/", parsed.path or "/")
    trailing_slash = raw_path.endswith("/")
    path = posixpath.normpath(raw_path)
    if not path.startswith("/"):
        path = f"/{path}"
    if trailing_slash and path != "/":
        path = f"{path}/"

    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
    return urlunsplit((scheme, host_for_netloc, path, query, ""))
