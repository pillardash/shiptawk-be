from app.modules.integrations.events.normalizer import NormalizedEvent


def should_process_tracked_branch(tracked_branch: str | None, event: NormalizedEvent) -> bool:
    normalized_branch = tracked_branch.strip() if tracked_branch is not None else ""
    if not normalized_branch or event.event_type == "release":
        return True
    if not event.branch_name:
        return False
    return normalized_branch == event.branch_name
