from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

SupportedEventType = Literal["push", "release", "pull_request_merged"]
Payload = Mapping[str, object]


class PushCommitContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    sha: str
    message: str
    url: str
    timestamp: str
    author: str | None
    files_changed: list[str]


class PushEnrichmentContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    commits: list[PushCommitContext]
    all_files_changed: list[str]


class NormalizedEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: SupportedEventType
    repo_full_name: str
    title: str
    description: str
    url: str
    occurred_at: str
    sha: str | None
    branch_name: str | None
    touched_paths: list[str]
    dedupe_key: str
    push_context: PushEnrichmentContext | None = None


def _record(value: object | None) -> Payload | None:
    return value if isinstance(value, Mapping) else None


def _string(record: Payload, key: str) -> str | None:
    value = record.get(key)
    return value if isinstance(value, str) else None


def _boolean(record: Payload, key: str) -> bool | None:
    value = record.get(key)
    return value if isinstance(value, bool) else None


def _strings(record: Payload, key: str) -> list[str]:
    value = record.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


class EventNormalizer:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def normalize(
        self, event_name: str, payload: Payload, repo_full_name: str
    ) -> NormalizedEvent | None:
        if event_name == "push":
            return self._normalize_push(payload, repo_full_name)
        if event_name == "release":
            return self._normalize_release(payload, repo_full_name)
        if event_name == "pull_request":
            return self._normalize_pull_request(payload, repo_full_name)
        return None

    def _now(self) -> str:
        value = self._clock().astimezone(UTC)
        return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _dedupe_key(
        event_type: SupportedEventType,
        repo_full_name: str,
        occurred_at: str,
        url: str,
        sha: str | None,
    ) -> str:
        return "|".join((event_type, repo_full_name, sha or "", occurred_at, url))

    def _extract_commits(self, payload: Payload) -> list[PushCommitContext]:
        values = payload.get("commits")
        if not isinstance(values, list):
            return []

        commits: list[PushCommitContext] = []
        for value in values:
            commit = _record(value)
            if commit is None:
                continue
            sha = _string(commit, "id") or ""
            if not sha:
                continue
            author = _record(commit.get("author"))
            files = _unique(
                _strings(commit, "added")
                + _strings(commit, "modified")
                + _strings(commit, "removed")
            )
            commits.append(
                PushCommitContext(
                    sha=sha,
                    message=_string(commit, "message") or "",
                    url=_string(commit, "url") or "",
                    timestamp=(
                        timestamp
                        if (timestamp := _string(commit, "timestamp")) is not None
                        else self._now()
                    ),
                    author=_string(author, "name") if author is not None else None,
                    files_changed=files,
                )
            )
        return commits

    @staticmethod
    def _touched_paths(payload: Payload) -> list[str]:
        values = payload.get("commits")
        if not isinstance(values, list):
            return []
        paths: list[str] = []
        for value in values:
            commit = _record(value)
            if commit is not None:
                paths.extend(_strings(commit, "added"))
                paths.extend(_strings(commit, "modified"))
                paths.extend(_strings(commit, "removed"))
        return _unique(paths)

    @staticmethod
    def _branch(ref: str | None) -> str | None:
        prefix = "refs/heads/"
        if ref is None or not ref.startswith(prefix):
            return None
        return ref[len(prefix) :] or None

    def _normalize_push(self, payload: Payload, repo_full_name: str) -> NormalizedEvent:
        commits = self._extract_commits(payload)
        selected = commits[-1] if commits else None
        head = _record(payload.get("head_commit"))
        title = selected.message if selected else (_string(head, "message") if head else None)
        sha = selected.sha if selected else (_string(head, "id") if head else None)
        url = selected.url if selected else (_string(head, "url") if head else None)
        timestamp = (
            selected.timestamp if selected else (_string(head, "timestamp") if head else None)
        )
        occurred_at = timestamp if timestamp is not None else self._now()
        resolved_url = url if url is not None else f"https://github.com/{repo_full_name}"
        resolved_title = title if title is not None else "Code pushed"
        touched_paths = self._touched_paths(payload)
        return NormalizedEvent(
            event_type="push",
            repo_full_name=repo_full_name,
            title=resolved_title,
            description=resolved_title,
            url=resolved_url,
            occurred_at=occurred_at,
            sha=sha,
            branch_name=self._branch(_string(payload, "ref")),
            touched_paths=touched_paths,
            dedupe_key=self._dedupe_key("push", repo_full_name, occurred_at, resolved_url, sha),
            push_context=PushEnrichmentContext(commits=commits, all_files_changed=touched_paths),
        )

    def _normalize_release(self, payload: Payload, repo_full_name: str) -> NormalizedEvent | None:
        release = _record(payload.get("release"))
        if release is None:
            return None
        title = _string(release, "name")
        description = _string(release, "body")
        url = _string(release, "html_url")
        published_at = _string(release, "published_at")
        occurred_at = published_at if published_at is not None else self._now()
        resolved_title = title if title is not None else "New release"
        resolved_description = description if description is not None else "Release published"
        resolved_url = url if url is not None else f"https://github.com/{repo_full_name}/releases"
        return NormalizedEvent(
            event_type="release",
            repo_full_name=repo_full_name,
            title=resolved_title,
            description=resolved_description,
            url=resolved_url,
            occurred_at=occurred_at,
            sha=None,
            branch_name=None,
            touched_paths=[],
            dedupe_key=self._dedupe_key("release", repo_full_name, occurred_at, resolved_url, None),
        )

    def _normalize_pull_request(
        self, payload: Payload, repo_full_name: str
    ) -> NormalizedEvent | None:
        pull_request = _record(payload.get("pull_request"))
        if pull_request is None:
            return None
        if _string(payload, "action") != "closed" or _boolean(pull_request, "merged") is not True:
            return None
        title = _string(pull_request, "title")
        description = _string(pull_request, "body")
        url = _string(pull_request, "html_url")
        merged_at = _string(pull_request, "merged_at")
        occurred_at = merged_at if merged_at is not None else self._now()
        base = _record(pull_request.get("base"))
        resolved_title = title if title is not None else "Pull request merged"
        resolved_description = description if description is not None else "PR merged"
        resolved_url = url if url is not None else f"https://github.com/{repo_full_name}/pulls"
        return NormalizedEvent(
            event_type="pull_request_merged",
            repo_full_name=repo_full_name,
            title=resolved_title,
            description=resolved_description,
            url=resolved_url,
            occurred_at=occurred_at,
            sha=None,
            branch_name=_string(base, "ref") if base is not None else None,
            touched_paths=[],
            dedupe_key=self._dedupe_key(
                "pull_request_merged", repo_full_name, occurred_at, resolved_url, None
            ),
        )
