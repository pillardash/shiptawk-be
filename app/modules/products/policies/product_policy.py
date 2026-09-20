from app.modules.products.policies.website_url_policy import (
    validate_public_website_url as validate_website_url,
)


def validate_public_website_url(value: str) -> str:
    if not value:
        return value
    try:
        return validate_website_url(value)
    except ValueError as exc:
        raise ValueError("websiteUrl must be a public HTTP(S) URL") from exc
