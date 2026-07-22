from collections.abc import AsyncGenerator, Generator
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import StaticPool, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.exception_handlers import register_exception_handlers
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.domains.users.models import User
from app.domains.users.router import router as user_settings_router
from app.domains.users.schemas import UserGenerationSettingsUpdate
from app.domains.users.service import update_generation_settings

VOICE_PROFILE = {
    "toneModes": ["technical", "direct"],
    "phrasesToAvoid": ["  game changer  "],
    "audiencePreference": "  engineering leaders  ",
    "styleSamples": ["  A concise release note.  "],
}
SAFETY_SETTINGS = {
    "blockedTerms": ["  guaranteed  "],
    "blockedTopics": ["  unverified revenue  "],
    "ignoredPaths": ["  vendor/**  "],
    "preferredEventTypes": ["push", "release"],
    "maxDraftsPerWeek": 8,
    "privateRepoMode": "conservative",
}


@pytest.fixture
def user_settings_client() -> Generator[tuple[TestClient, User], None, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with sessions() as db:
            yield db

    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(user_settings_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db

    async def create_data() -> User:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with sessions() as db:
            user = User(
                email="settings@example.com",
                github_id="provider-subject",
                github_username="octocat",
                github_installation_id=12345,
                tone_preference="casual",
                voice_profile={
                    "tone_modes": ["concise", "direct"],
                    "phrases_to_avoid": [],
                    "audience_preference": "developers",
                    "style_samples": [],
                },
                safety_settings={
                    "blocked_terms": [],
                    "blocked_topics": [],
                    "ignored_paths": [],
                    "preferred_event_types": ["push", "release", "pull_request_merged"],
                    "max_drafts_per_week": 5,
                    "private_repo_mode": "normal",
                },
                email_notifications_enabled=False,
                achievement_digest_enabled=True,
                achievement_digest_frequency="monthly",
                achievement_digest_prompt_dismissed=True,
                achievement_digest_onboarding_seen=True,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            return user

    async def cleanup() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

    with TestClient(app) as client:
        assert client.portal is not None
        user = client.portal.call(create_data)
        yield client, user
        client.portal.call(cleanup)


def auth_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def test_generation_settings_requires_authentication(
    user_settings_client: tuple[TestClient, User],
) -> None:
    client, _ = user_settings_client

    assert client.get("/api/v1/user/settings").status_code == 401
    assert client.patch("/api/v1/user/settings", json={}).status_code == 401


def test_get_generation_settings_is_camel_case_and_exposes_no_credentials(
    user_settings_client: tuple[TestClient, User],
) -> None:
    client, user = user_settings_client

    response = client.get("/api/v1/user/settings", headers=auth_headers(user))

    assert response.status_code == 200
    assert response.json() == {
        "tonePreference": "casual",
        "voiceProfile": {
            "toneModes": ["concise", "direct"],
            "phrasesToAvoid": [],
            "audiencePreference": "developers",
            "styleSamples": [],
        },
        "safetySettings": {
            "blockedTerms": [],
            "blockedTopics": [],
            "ignoredPaths": [],
            "preferredEventTypes": ["push", "release", "pull_request_merged"],
            "maxDraftsPerWeek": 5,
            "privateRepoMode": "normal",
        },
        "emailNotificationsEnabled": False,
        "achievementDigestEnabled": True,
        "achievementDigestFrequency": "monthly",
        "achievementDigestPromptDismissed": True,
        "achievementDigestOnboardingSeen": True,
    }
    serialized = response.text.lower()
    assert "github" not in serialized
    assert "token" not in serialized
    assert "credential" not in serialized


def test_patch_replaces_validated_settings_and_preserves_omitted_preferences(
    user_settings_client: tuple[TestClient, User],
) -> None:
    client, user = user_settings_client
    payload = {
        "tonePreference": "technical",
        "voiceProfile": VOICE_PROFILE,
        "safetySettings": SAFETY_SETTINGS,
        "emailNotificationsEnabled": True,
    }

    response = client.patch("/api/v1/user/settings", headers=auth_headers(user), json=payload)

    assert response.status_code == 200
    assert response.json()["voiceProfile"]["phrasesToAvoid"] == ["game changer"]
    assert response.json()["safetySettings"]["ignoredPaths"] == ["vendor/**"]
    assert response.json()["emailNotificationsEnabled"] is True
    assert response.json()["achievementDigestEnabled"] is True
    assert response.json()["achievementDigestFrequency"] == "monthly"

    stored = client.get("/api/v1/user/settings", headers=auth_headers(user))
    assert stored.json() == response.json()


@pytest.mark.parametrize(
    "field,value",
    [
        ("tonePreference", "formal"),
        ("voiceProfile.toneModes", []),
        ("voiceProfile.phrasesToAvoid", ["x" * 81]),
        ("safetySettings.preferredEventTypes", []),
        ("safetySettings.maxDraftsPerWeek", 101),
    ],
)
def test_patch_preserves_frontend_validation_boundaries(
    user_settings_client: tuple[TestClient, User], field: str, value: object
) -> None:
    client, user = user_settings_client
    payload: dict[str, object] = {
        "tonePreference": "hype",
        "voiceProfile": dict(VOICE_PROFILE),
        "safetySettings": dict(SAFETY_SETTINGS),
    }
    target = payload
    parts = field.split(".")
    for part in parts[:-1]:
        target = cast(dict[str, object], target[part])
    target[parts[-1]] = value

    response = client.patch("/api/v1/user/settings", headers=auth_headers(user), json=payload)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


async def test_update_rolls_back_when_commit_fails() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessions() as db:
        user = User(email="rollback@example.com")
        db.add(user)
        await db.commit()
        user_id = user.id
        settings = UserGenerationSettingsUpdate.model_validate(
            {
                "tonePreference": "technical",
                "voiceProfile": VOICE_PROFILE,
                "safetySettings": SAFETY_SETTINGS,
            }
        )
        original_commit = db.commit

        async def fail_commit() -> None:
            raise RuntimeError("database write failed")

        db.commit = fail_commit  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="database write failed"):
            await update_generation_settings(db, user, settings)
        db.commit = original_commit  # type: ignore[method-assign]

        persisted = await db.scalar(select(User).where(User.id == user_id))
        assert persisted is not None
        assert persisted.tone_preference == "casual"

    await engine.dispose()
