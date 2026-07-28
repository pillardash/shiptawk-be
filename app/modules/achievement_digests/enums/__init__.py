from enum import StrEnum


class AchievementDigestFrequency(StrEnum):
    weekly = "weekly"
    monthly = "monthly"


class AchievementDigestFeedbackAction(StrEnum):
    useful = "useful"
    not_useful = "not_useful"
    copied = "copied"
    converted_to_draft = "converted_to_draft"
    dismissed = "dismissed"


__all__ = ["AchievementDigestFeedbackAction", "AchievementDigestFrequency"]
