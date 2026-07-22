from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import httpx
from jose import jwt


class GitHubAppError(Exception):
    """A controlled GitHub App transport or validation failure."""


@dataclass(frozen=True, slots=True)
class GitHubInstallation:
    id: int
    account_login: str


@dataclass(frozen=True, slots=True)
class GitHubRepository:
    id: int
    name: str
    full_name: str
    private: bool
    default_branch: str | None
    language: str | None
    description: str | None


class GitHubAppProvider(Protocol):
    async def verify_installation(self, installation_id: int) -> GitHubInstallation: ...

    async def list_repositories(self, installation_id: int) -> list[GitHubRepository]: ...


class FakeGitHubAppProvider:
    def __init__(
        self, *, installation: GitHubInstallation, repositories: list[GitHubRepository]
    ) -> None:
        self.installation = installation
        self.repositories = repositories
        self.verify_calls = 0
        self.list_calls = 0

    async def verify_installation(self, installation_id: int) -> GitHubInstallation:
        self.verify_calls += 1
        if installation_id != self.installation.id:
            raise GitHubAppError("GitHub App installation was not found.")
        return self.installation

    async def list_repositories(self, installation_id: int) -> list[GitHubRepository]:
        self.list_calls += 1
        await self.verify_installation(installation_id)
        self.verify_calls -= 1
        return list(self.repositories)


class HttpGitHubAppProvider:
    api_base_url = "https://api.github.com"

    def __init__(
        self,
        *,
        app_id: int,
        private_key: str,
        timeout_seconds: float = 10.0,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.app_id = app_id
        self.private_key = private_key
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
        self.http_transport = http_transport

    def _app_jwt(self) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "iat": now - timedelta(seconds=30),
                "exp": now + timedelta(minutes=9),
                "iss": str(self.app_id),
            },
            self.private_key,
            algorithm="RS256",
        )

    async def _request(
        self, method: str, path: str, *, bearer: str, json: dict[str, object] | None = None
    ) -> object:
        try:
            async with httpx.AsyncClient(
                base_url=self.api_base_url,
                timeout=self.timeout,
                transport=self.http_transport,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {bearer}",
                    "User-Agent": "Shiptawk",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            ) as client:
                response = await client.request(method, path, json=json)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            raise GitHubAppError("GitHub App is temporarily unavailable.") from exc

    async def verify_installation(self, installation_id: int) -> GitHubInstallation:
        payload = await self._request(
            "GET", f"/app/installations/{installation_id}", bearer=self._app_jwt()
        )
        if not isinstance(payload, dict) or payload.get("id") != installation_id:
            raise GitHubAppError("GitHub returned an invalid installation response.")
        account = payload.get("account")
        if not isinstance(account, dict) or not isinstance(account.get("login"), str):
            raise GitHubAppError("GitHub returned an invalid installation response.")
        return GitHubInstallation(id=installation_id, account_login=account["login"])

    async def _installation_token(self, installation_id: int) -> str:
        payload = await self._request(
            "POST", f"/app/installations/{installation_id}/access_tokens", bearer=self._app_jwt()
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("token"), str):
            raise GitHubAppError("GitHub returned an invalid installation token response.")
        token = payload["token"]
        assert isinstance(token, str)
        return token

    async def list_repositories(self, installation_id: int) -> list[GitHubRepository]:
        token = await self._installation_token(installation_id)
        repositories: list[GitHubRepository] = []
        for page in range(1, 101):
            payload = await self._request(
                "GET",
                f"/installation/repositories?per_page=100&page={page}",
                bearer=token,
            )
            items = payload.get("repositories") if isinstance(payload, dict) else None
            if not isinstance(items, list) or len(items) > 100:
                raise GitHubAppError("GitHub returned an invalid repository response.")
            for item in items:
                if not isinstance(item, dict):
                    raise GitHubAppError("GitHub returned an invalid repository response.")
                repo_id = item.get("id")
                name = item.get("name")
                full_name = item.get("full_name")
                if (
                    not isinstance(repo_id, int)
                    or not isinstance(name, str)
                    or not isinstance(full_name, str)
                ):
                    raise GitHubAppError("GitHub returned an invalid repository response.")
                repositories.append(
                    GitHubRepository(
                        id=repo_id,
                        name=name,
                        full_name=full_name,
                        private=bool(item.get("private")),
                        default_branch=(
                            item.get("default_branch")
                            if isinstance(item.get("default_branch"), str)
                            else None
                        ),
                        language=(
                            item.get("language") if isinstance(item.get("language"), str) else None
                        ),
                        description=(
                            item.get("description")
                            if isinstance(item.get("description"), str)
                            else None
                        ),
                    )
                )
            if len(items) < 100:
                return repositories
        raise GitHubAppError("GitHub repository pagination exceeded the safety limit.")
