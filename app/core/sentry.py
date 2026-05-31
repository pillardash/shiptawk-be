import sentry_sdk

from app.core.config import Settings


def configure_sentry(settings: Settings) -> None:
    if not settings.sentry_is_enabled:
        return

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release=settings.app_version,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        profiles_sample_rate=settings.sentry_profiles_sample_rate,
        send_default_pii=settings.sentry_send_default_pii,
    )


def set_sentry_request_context(request_id: str) -> None:
    sentry_sdk.set_tag("request_id", request_id)


def capture_exception(exc: Exception) -> None:
    sentry_sdk.capture_exception(exc)
