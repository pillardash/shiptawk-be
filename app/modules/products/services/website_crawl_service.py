import hashlib
from datetime import UTC, datetime
from urllib.parse import urljoin, urlsplit
from uuid import UUID
from xml.etree import ElementTree

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.modules.products.enums.product_profile_enum import ProductProfileStatus
from app.modules.products.enums.website_intelligence_enum import (
    WebsiteCapabilityMappingStatus,
    WebsiteCrawlResultStatus,
    WebsiteCrawlRunStatus,
    WebsitePageStatus,
)
from app.modules.products.models import (
    ProductProfile,
    WebsiteCapabilityMapping,
    WebsiteCrawlResult,
    WebsiteCrawlRun,
    WebsitePage,
    WebsiteSource,
)
from app.modules.products.policies.website_crawl_policy import MAX_CRAWL_PAGES, select_crawl_urls
from app.modules.products.policies.website_relevance_policy import (
    RelevancePage,
    capability_is_represented,
    most_relevant_page,
)
from app.modules.products.policies.website_url_policy import normalize_website_url
from app.modules.products.providers.website_fetch_provider import (
    WebsiteFetchBlockedError,
    WebsiteFetchError,
    WebsiteFetchProvider,
    WebsiteFetchResult,
)
from app.modules.products.services.website_extraction_service import extract_website_page


class WebsiteCrawlService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        provider: WebsiteFetchProvider,
    ) -> None:
        self._sessions = sessions
        self._provider = provider

    async def process(self, run_id: UUID) -> dict[str, object]:
        try:
            return await self._process(run_id)
        except Exception as exc:
            async with self._sessions() as db:
                run = await db.get(WebsiteCrawlRun, run_id)
                if run is not None and run.status not in {
                    WebsiteCrawlRunStatus.succeeded,
                    WebsiteCrawlRunStatus.cancelled,
                }:
                    await self._fail_run(
                        db,
                        run,
                        "crawl_unexpected_failure",
                        type(exc).__name__,
                    )
            raise

    async def _process(self, run_id: UUID) -> dict[str, object]:
        async with self._sessions() as db:
            context = await self._load_context(db, run_id)
            if context is None:
                raise ValueError("Website crawl run was not found.")
            run, source = context
            if run.status == WebsiteCrawlRunStatus.succeeded:
                return self._result(run)
            if run.status == WebsiteCrawlRunStatus.cancelled:
                return {"status": "cancelled"}
            now = datetime.now(UTC)
            run.status = WebsiteCrawlRunStatus.running
            run.started_at = run.started_at or now
            run.completed_at = None
            run.error_code = None
            run.error_message = None
            await db.commit()

            base_url = normalize_website_url(source.normalized_base_url)
            disallowed_paths = await self._robots_disallowed_paths(base_url)
            if self._is_robots_blocked(base_url, disallowed_paths):
                error = WebsiteFetchBlockedError("robots_blocked")
                await self._persist_failure(db, run, source, base_url, error)
                await self._fail_run(db, run, error.code, str(error))
                return {"status": "failed", "errorCode": error.code}
            try:
                base_response = await self._provider.fetch(base_url)
            except WebsiteFetchError as exc:
                await self._persist_failure(db, run, source, base_url, exc)
                await self._fail_run(db, run, exc.code, str(exc))
                return {"status": "failed", "errorCode": exc.code}

            if base_response.status_code >= 400:
                code = f"http_{base_response.status_code}"
                await self._persist_http_failure(db, run, source, base_url, base_response)
                await self._fail_run(db, run, code, code)
                return {"status": "failed", "errorCode": code}
            if not self._same_site(base_url, base_response.final_url):
                error = WebsiteFetchBlockedError("cross_site_redirect")
                await self._persist_failure(db, run, source, base_url, error)
                await self._fail_run(db, run, error.code, str(error))
                return {"status": "failed", "errorCode": error.code}

            base_extraction = extract_website_page(
                base_response.body.decode("utf-8", errors="replace"), base_response.final_url
            )
            candidates = list(base_extraction.internal_links)
            candidates.extend(
                url for url in (source.blog_url, source.documentation_url) if url is not None
            )
            page_limit = min(run.page_limit, MAX_CRAWL_PAGES)
            sitemap_fetches = await self._discover_sitemaps(
                source,
                candidates,
                max_fetches=max(0, page_limit - 1),
            )
            targets = select_crawl_urls(
                base_url,
                candidates,
                max_pages=max(1, page_limit - sitemap_fetches),
            )
            run.pages_discovered = len(targets)

            succeeded = 0
            failed = 0
            for target in targets:
                if self._is_robots_blocked(target, disallowed_paths):
                    await self._persist_failure(
                        db,
                        run,
                        source,
                        target,
                        WebsiteFetchBlockedError("robots_blocked"),
                    )
                    failed += 1
                    continue
                try:
                    fetched = (
                        base_response if target == base_url else await self._provider.fetch(target)
                    )
                    if not self._same_site(base_url, fetched.final_url):
                        await self._persist_failure(
                            db,
                            run,
                            source,
                            target,
                            WebsiteFetchBlockedError("cross_site_redirect"),
                        )
                        failed += 1
                    elif fetched.status_code >= 400:
                        await self._persist_http_failure(db, run, source, target, fetched)
                        failed += 1
                    else:
                        await self._persist_success(db, run, source, target, fetched)
                        succeeded += 1
                except WebsiteFetchError as exc:
                    await self._persist_failure(db, run, source, target, exc)
                    failed += 1

            await db.flush()
            await self._map_capabilities(db, run)

            completed_at = datetime.now(UTC)
            run.pages_succeeded = succeeded
            run.pages_failed = failed
            run.status = WebsiteCrawlRunStatus.succeeded
            run.completed_at = completed_at
            run.summary = {"pagesSucceeded": succeeded, "pagesFailed": failed}
            source.last_successful_crawl_at = completed_at
            await db.commit()
            return self._result(run)

    async def _robots_disallowed_paths(self, base_url: str) -> tuple[str, ...]:
        robots_url = urljoin(base_url, "/robots.txt")
        try:
            response = await self._provider.fetch(robots_url)
        except WebsiteFetchError:
            return ()
        if response.status_code >= 400 or response.content_type != "text/plain":
            return ()
        active = False
        paths: list[str] = []
        for raw_line in response.body.decode("utf-8", errors="replace").splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, value = (part.strip() for part in line.split(":", 1))
            if field.lower() == "user-agent":
                active = value == "*" or value.lower() == "shiptawkwebsitecrawler"
            elif active and field.lower() == "disallow" and value:
                paths.append(value)
        return tuple(paths)

    @staticmethod
    def _is_robots_blocked(url: str, disallowed_paths: tuple[str, ...]) -> bool:
        path = urlsplit(url).path or "/"
        return any(path.startswith(prefix) for prefix in disallowed_paths)

    @staticmethod
    def _same_site(base_url: str, candidate_url: str) -> bool:
        def normalized_host(url: str) -> str:
            host = (urlsplit(url).hostname or "").lower().rstrip(".")
            return host.removeprefix("www.")

        return normalized_host(base_url) == normalized_host(candidate_url)

    @staticmethod
    async def _map_capabilities(db: AsyncSession, run: WebsiteCrawlRun) -> None:
        profile = await db.scalar(
            select(ProductProfile).where(
                ProductProfile.workspace_id == run.workspace_id,
                ProductProfile.product_id == run.product_id,
                ProductProfile.status == ProductProfileStatus.approved,
            )
        )
        if profile is None or profile.version is None or not profile.important_capabilities:
            return
        rows = list(
            (
                await db.execute(
                    select(WebsitePage, WebsiteCrawlResult)
                    .join(
                        WebsiteCrawlResult,
                        (WebsiteCrawlResult.workspace_id == WebsitePage.workspace_id)
                        & (WebsiteCrawlResult.product_id == WebsitePage.product_id)
                        & (WebsiteCrawlResult.page_id == WebsitePage.id),
                    )
                    .where(
                        WebsiteCrawlResult.workspace_id == run.workspace_id,
                        WebsiteCrawlResult.product_id == run.product_id,
                        WebsiteCrawlResult.crawl_run_id == run.id,
                        WebsiteCrawlResult.status == WebsiteCrawlResultStatus.succeeded,
                    )
                )
            ).tuples()
        )
        relevance_pages = [
            RelevancePage(
                url=page.canonical_url,
                title=result.title or "",
                headings=tuple(
                    str(heading.get("text", ""))
                    for heading in result.headings
                    if heading.get("text")
                ),
                text=result.extracted_text or "",
            )
            for page, result in rows
        ]
        pages_by_url = {page.canonical_url: page for page, _ in rows}
        for capability in profile.important_capabilities:
            ranked = most_relevant_page(capability, relevance_pages)
            represented = capability_is_represented(capability, relevance_pages)
            status = (
                WebsiteCapabilityMappingStatus.missing
                if ranked is None
                else WebsiteCapabilityMappingStatus.represented
                if represented
                else WebsiteCapabilityMappingStatus.partial
            )
            page = pages_by_url.get(ranked.page.url) if ranked is not None else None
            capability_key = hashlib.sha256(capability.strip().lower().encode()).hexdigest()[:32]
            mapping = await db.scalar(
                select(WebsiteCapabilityMapping).where(
                    WebsiteCapabilityMapping.workspace_id == run.workspace_id,
                    WebsiteCapabilityMapping.crawl_run_id == run.id,
                    WebsiteCapabilityMapping.capability_key == capability_key,
                )
            )
            if mapping is None:
                mapping = WebsiteCapabilityMapping(
                    workspace_id=run.workspace_id,
                    product_id=run.product_id,
                    source_id=run.source_id,
                    crawl_run_id=run.id,
                    capability_key=capability_key,
                    capability_name=capability,
                    mapping_version="lexical.v1",
                    status=status,
                    relevance_score=round((ranked.score if ranked else 0) * 100),
                    confidence_score=round((ranked.score if ranked else 0) * 100),
                    page_id=page.id if page else None,
                    evidence={},
                )
                db.add(mapping)
            mapping.status = status
            mapping.page_id = page.id if page else None
            mapping.relevance_score = round((ranked.score if ranked else 0) * 100)
            mapping.confidence_score = mapping.relevance_score
            mapping.evidence = {
                "profileId": str(profile.id),
                "profileVersion": profile.version,
                "matchedTerms": list(ranked.matched_terms) if ranked else [],
                "method": "lexical",
            }

    @staticmethod
    async def _load_context(
        db: AsyncSession, run_id: UUID
    ) -> tuple[WebsiteCrawlRun, WebsiteSource] | None:
        statement = (
            select(WebsiteCrawlRun, WebsiteSource)
            .join(
                WebsiteSource,
                (WebsiteSource.id == WebsiteCrawlRun.source_id)
                & (WebsiteSource.workspace_id == WebsiteCrawlRun.workspace_id)
                & (WebsiteSource.product_id == WebsiteCrawlRun.product_id),
            )
            .where(WebsiteCrawlRun.id == run_id)
            .with_for_update()
        )
        row = (await db.execute(statement)).one_or_none()
        return None if row is None else (row[0], row[1])

    async def _discover_sitemaps(
        self,
        source: WebsiteSource,
        candidates: list[str],
        *,
        max_fetches: int,
    ) -> int:
        base_host = urlsplit(source.normalized_base_url).hostname
        pending = list(source.sitemap_urls) or [
            urljoin(source.normalized_base_url, "/sitemap.xml"),
            urljoin(source.normalized_base_url, "/sitemap_index.xml"),
        ]
        visited: set[str] = set()
        while pending and len(visited) < max_fetches:
            raw_url = pending.pop(0)
            try:
                sitemap_url = normalize_website_url(raw_url)
            except ValueError:
                continue
            if sitemap_url in visited or urlsplit(sitemap_url).hostname != base_host:
                continue
            visited.add(sitemap_url)
            try:
                fetched = await self._provider.fetch(sitemap_url)
                if fetched.status_code >= 400 or fetched.content_type not in {
                    "application/xml",
                    "text/xml",
                }:
                    continue
                root = ElementTree.fromstring(fetched.body)
            except (WebsiteFetchError, ElementTree.ParseError):
                continue
            locations = [
                element.text.strip()
                for element in root.iter()
                if element.tag.rsplit("}", 1)[-1] == "loc" and element.text and element.text.strip()
            ]
            if root.tag.rsplit("}", 1)[-1] == "sitemapindex":
                pending.extend(locations)
            else:
                candidates.extend(locations)
        return len(visited)

    async def _page(
        self,
        db: AsyncSession,
        run: WebsiteCrawlRun,
        source: WebsiteSource,
        url: str,
        canonical_url: str,
        status: WebsitePageStatus,
    ) -> WebsitePage:
        page = await db.scalar(
            select(WebsitePage).where(
                WebsitePage.workspace_id == run.workspace_id,
                WebsitePage.source_id == source.id,
                WebsitePage.canonical_url == canonical_url,
            )
        )
        if page is None:
            page = WebsitePage(
                workspace_id=run.workspace_id,
                product_id=run.product_id,
                source_id=source.id,
                first_discovered_run_id=run.id,
                url=url,
                canonical_url=canonical_url,
                url_fingerprint=hashlib.sha256(canonical_url.encode()).hexdigest(),
                status=status,
            )
            db.add(page)
            await db.flush()
        else:
            page.url = url
            page.status = status
        return page

    async def _result_record(
        self, db: AsyncSession, run: WebsiteCrawlRun, page: WebsitePage
    ) -> WebsiteCrawlResult:
        result = await db.scalar(
            select(WebsiteCrawlResult).where(
                WebsiteCrawlResult.workspace_id == run.workspace_id,
                WebsiteCrawlResult.crawl_run_id == run.id,
                WebsiteCrawlResult.page_id == page.id,
            )
        )
        if result is None:
            result = WebsiteCrawlResult(
                workspace_id=run.workspace_id,
                product_id=run.product_id,
                source_id=run.source_id,
                crawl_run_id=run.id,
                page_id=page.id,
                status=WebsiteCrawlResultStatus.failed,
            )
            db.add(result)
        return result

    async def _persist_success(
        self,
        db: AsyncSession,
        run: WebsiteCrawlRun,
        source: WebsiteSource,
        target: str,
        fetched: WebsiteFetchResult,
    ) -> None:
        extraction = extract_website_page(
            fetched.body.decode("utf-8", errors="replace"), fetched.final_url
        )
        canonical_url = extraction.canonical_url or extraction.url
        page = await self._page(db, run, source, target, canonical_url, WebsitePageStatus.active)
        result = await self._result_record(db, run, page)
        result.status = WebsiteCrawlResultStatus.succeeded
        result.final_url = fetched.final_url
        result.http_status = fetched.status_code
        result.content_type = fetched.content_type
        result.title = extraction.title
        result.meta_description = extraction.meta_description
        result.headings = [{"text": heading} for heading in extraction.headings]
        result.extracted_text = extraction.text
        result.content_fingerprint = extraction.fingerprint
        result.is_indexable = extraction.is_indexable
        result.indexability_reasons = [] if extraction.is_indexable else ["noindex"]
        result.fetched_at = datetime.now(UTC)
        result.error_code = None
        result.error_message = None

    async def _persist_http_failure(
        self,
        db: AsyncSession,
        run: WebsiteCrawlRun,
        source: WebsiteSource,
        target: str,
        fetched: WebsiteFetchResult,
    ) -> None:
        status = (
            WebsitePageStatus.missing if fetched.status_code == 404 else WebsitePageStatus.failed
        )
        page = await self._page(db, run, source, target, target, status)
        result = await self._result_record(db, run, page)
        result.status = WebsiteCrawlResultStatus.failed
        result.final_url = fetched.final_url
        result.http_status = fetched.status_code
        result.content_type = fetched.content_type
        result.fetched_at = datetime.now(UTC)
        result.content_fingerprint = None
        result.error_code = f"http_{fetched.status_code}"
        result.error_message = result.error_code

    async def _persist_failure(
        self,
        db: AsyncSession,
        run: WebsiteCrawlRun,
        source: WebsiteSource,
        target: str,
        error: WebsiteFetchError,
    ) -> None:
        page_status = (
            WebsitePageStatus.blocked
            if isinstance(error, WebsiteFetchBlockedError)
            else WebsitePageStatus.failed
        )
        page = await self._page(db, run, source, target, target, page_status)
        result = await self._result_record(db, run, page)
        result.status = (
            WebsiteCrawlResultStatus.blocked
            if isinstance(error, WebsiteFetchBlockedError)
            else WebsiteCrawlResultStatus.failed
        )
        result.final_url = None
        result.http_status = None
        result.content_type = None
        result.fetched_at = datetime.now(UTC)
        result.content_fingerprint = None
        result.error_code = error.code
        result.error_message = str(error)

    @staticmethod
    async def _fail_run(
        db: AsyncSession, run: WebsiteCrawlRun, error_code: str, error_message: str
    ) -> None:
        run.pages_discovered = max(run.pages_discovered, 1)
        run.pages_failed = max(run.pages_failed, 1)
        run.status = WebsiteCrawlRunStatus.failed
        run.completed_at = datetime.now(UTC)
        run.error_code = error_code[:64]
        run.error_message = error_message
        await db.commit()

    @staticmethod
    def _result(run: WebsiteCrawlRun) -> dict[str, object]:
        return {
            "status": run.status.value,
            "pagesSucceeded": run.pages_succeeded,
            "pagesFailed": run.pages_failed,
        }


__all__ = ["WebsiteCrawlService"]
