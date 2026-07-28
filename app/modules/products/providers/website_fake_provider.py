from collections.abc import Mapping

from app.modules.products.providers.website_fetch_provider import (
    WebsiteFetchError,
    WebsiteFetchResult,
)


class FakeWebsiteFetchProvider:
    def __init__(self, responses: Mapping[str, WebsiteFetchResult | WebsiteFetchError]) -> None:
        self._responses = dict(responses)
        self.calls: list[str] = []

    async def fetch(self, url: str) -> WebsiteFetchResult:
        self.calls.append(url)
        response = self._responses.get(url)
        if response is None:
            raise WebsiteFetchError("fake_response_not_configured")
        if isinstance(response, WebsiteFetchError):
            raise response
        return response


__all__ = ["FakeWebsiteFetchProvider"]
