from urllib.parse import urlsplit

from app.modules.products.policies.website_url_policy import normalize_website_url
from app.modules.search_intelligence.enums.search_intelligence_enum import (
    SearchPropertyCompatibility,
    SearchPropertyType,
)


def classify_property_compatibility(
    *,
    property_type: SearchPropertyType,
    provider_property_id: str,
    normalized_website_url: str,
) -> SearchPropertyCompatibility:
    try:
        website = urlsplit(normalize_website_url(normalized_website_url))
    except ValueError:
        return SearchPropertyCompatibility.unknown
    if property_type is SearchPropertyType.domain:
        domain = provider_property_id.removeprefix("sc-domain:").lower().rstrip(".")
        if not domain or provider_property_id == domain:
            return SearchPropertyCompatibility.unknown
        host = (website.hostname or "").lower().rstrip(".")
        compatible = host == domain or host.endswith(f".{domain}")
        return (
            SearchPropertyCompatibility.compatible
            if compatible
            else SearchPropertyCompatibility.incompatible
        )
    try:
        prefix = urlsplit(normalize_website_url(provider_property_id))
    except ValueError:
        return SearchPropertyCompatibility.unknown
    same_origin = (website.scheme, website.hostname, website.port) == (
        prefix.scheme,
        prefix.hostname,
        prefix.port,
    )
    prefix_path = prefix.path.rstrip("/")
    path_matches = website.path == prefix_path or website.path.startswith(f"{prefix_path}/")
    return (
        SearchPropertyCompatibility.compatible
        if same_origin and path_matches
        else SearchPropertyCompatibility.incompatible
    )


__all__ = ["classify_property_compatibility"]
