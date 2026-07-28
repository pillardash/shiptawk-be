from app.modules.drafts.schemas.context import EffectiveGenerationContext
from app.modules.integrations.events import NormalizedEvent
from app.shared.exceptions import ConflictError


def ensure_regeneration_allowed(
    event: NormalizedEvent,
    context: EffectiveGenerationContext,
    repo_private: bool,
) -> None:
    text = f"{event.title} {event.description}".lower()
    if event.event_type not in context.preferred_event_types:
        raise ConflictError(
            "Regeneration is disabled for this event type.",
            code="draft_regeneration_event_disabled",
        )
    if repo_private and context.private_repo_mode == "conservative":
        raise ConflictError(
            "Regeneration is disabled for private repositories.",
            code="draft_regeneration_private_disabled",
        )
    if any(term.lower() in text for term in context.blocked_terms):
        raise ConflictError(
            "Regeneration is blocked by a configured term.",
            code="draft_regeneration_blocked",
        )
    if any(topic.lower() in text for topic in context.blocked_topics):
        raise ConflictError(
            "Regeneration is blocked by a configured topic.",
            code="draft_regeneration_blocked",
        )
    if (
        event.touched_paths
        and context.ignored_paths
        and all(
            any(path.startswith(ignored) for ignored in context.ignored_paths)
            for path in event.touched_paths
        )
    ):
        raise ConflictError(
            "Regeneration is blocked because the event only touched ignored paths.",
            code="draft_regeneration_ignored_paths",
        )
