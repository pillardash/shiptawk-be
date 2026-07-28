from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

WebsiteHealthStatus = Literal["healthy", "degraded", "failed", "unknown"]


@dataclass(frozen=True, slots=True)
class CrawlPageObservation:
    url: str
    status_code: int | None = None
    error: str | None = None
    blocked_reason: str | None = None


@dataclass(frozen=True, slots=True)
class WebsiteHealthSummary:
    status: WebsiteHealthStatus
    total_count: int
    healthy_count: int
    blocked_urls: tuple[str, ...]
    failures: tuple[tuple[str, str], ...]


def summarize_website_health(
    observations: Iterable[CrawlPageObservation],
) -> WebsiteHealthSummary:
    """Summarize crawl outcomes without hiding blocked or failed pages."""
    rows = sorted(observations, key=lambda observation: observation.url)
    healthy_count = 0
    blocked_urls: list[str] = []
    failures: list[tuple[str, str]] = []
    for observation in rows:
        if observation.blocked_reason is not None or observation.status_code in {401, 403, 429}:
            blocked_urls.append(observation.url)
        elif observation.error is not None:
            failures.append((observation.url, observation.error))
        elif observation.status_code is not None and 200 <= observation.status_code < 400:
            healthy_count += 1
        else:
            reason = (
                f"HTTP {observation.status_code}"
                if observation.status_code is not None
                else "No crawl result"
            )
            failures.append((observation.url, reason))

    if not rows:
        status: WebsiteHealthStatus = "unknown"
    elif healthy_count == len(rows):
        status = "healthy"
    elif healthy_count == 0:
        status = "failed"
    else:
        status = "degraded"
    return WebsiteHealthSummary(
        status=status,
        total_count=len(rows),
        healthy_count=healthy_count,
        blocked_urls=tuple(blocked_urls),
        failures=tuple(failures),
    )
