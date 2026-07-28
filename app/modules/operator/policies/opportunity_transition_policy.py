class InvalidOpportunityTransitionError(ValueError):
    pass


def require_transition(current: str, target: str, *, internal: bool) -> None:
    allowed = current == "open" and target == "dismissed"
    allowed = allowed or (internal and current in ("open", "planned") and target == "superseded")
    if not allowed:
        raise InvalidOpportunityTransitionError(
            f"Invalid opportunity transition: {current} -> {target}."
        )
