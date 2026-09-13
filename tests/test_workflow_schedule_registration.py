from typing import Any, cast

import inngest

from app.workflows.runtime import DeferredOnboardingWorkflow, create_workflow_functions
from app.workflows.unavailable import UnavailableGitHubConsumerWorkflow


def function_ids(functions: list[object]) -> set[str]:
    return {cast(Any, function)._opts.local_id for function in functions}


def test_legacy_schedules_are_disabled_while_weekly_growth_defaults_to_enabled() -> None:
    functions = create_workflow_functions(
        client=inngest.Inngest(app_id="workflow-default-schedules-test"),
        github=UnavailableGitHubConsumerWorkflow(),
        onboarding=DeferredOnboardingWorkflow(),
        digests=None,
    ).functions

    assert function_ids(functions).isdisjoint(
        {
            "send-draft-digests",
            "send-weekly-achievement-digests",
            "send-monthly-achievement-digests",
            "send-weekly-repo-changelog-digests",
            "send-monthly-repo-changelog-digests",
        }
    )
    assert "schedule-weekly-growth-operators" in function_ids(functions)
    assert "deliver-weekly-growth-email" in function_ids(functions)


def test_legacy_opt_in_is_independent_from_weekly_growth_scheduling() -> None:
    functions = create_workflow_functions(
        client=inngest.Inngest(app_id="workflow-opted-in-schedules-test"),
        github=UnavailableGitHubConsumerWorkflow(),
        onboarding=DeferredOnboardingWorkflow(),
        digests=None,
        legacy_daily_draft_schedule_enabled=True,
        legacy_achievement_digest_schedules_enabled=True,
        legacy_repository_changelog_schedules_enabled=True,
        weekly_growth_schedule_enabled=False,
    ).functions
    ids = function_ids(functions)

    assert {
        "send-draft-digests",
        "send-weekly-achievement-digests",
        "send-monthly-achievement-digests",
        "send-weekly-repo-changelog-digests",
        "send-monthly-repo-changelog-digests",
    } <= ids
    assert "schedule-weekly-growth-operators" not in ids
    assert "deliver-weekly-growth-email" in ids
