import os
import urllib.request
from urllib.parse import urlsplit


def healthcheck_host() -> str:
    public_url = os.getenv("PUBLIC_BACKEND_URL", "")
    if public_url:
        hostname = urlsplit(public_url).hostname
        if hostname:
            return hostname
    allowed = os.getenv("ALLOWED_HOSTS", "localhost").split(",", maxsplit=1)[0].strip()
    return allowed or "localhost"


def main() -> None:
    port = os.getenv("PORT", "8000")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/health", headers={"Host": healthcheck_host()}
    )
    urllib.request.urlopen(request, timeout=3).read()


if __name__ == "__main__":
    main()
