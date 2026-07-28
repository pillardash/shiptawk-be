from enum import StrEnum


class OnboardingStep(StrEnum):
    connect_github = "connect_github"
    sync_repos = "sync_repos"
    track_repos = "track_repos"
    group_products = "group_products"
    product_context = "product_context"
    review_defaults = "review_defaults"
    complete = "complete"


class TonePreference(StrEnum):
    casual = "casual"
    technical = "technical"
    hype = "hype"


class VoiceToneMode(StrEnum):
    direct = "direct"
    technical = "technical"
    playful = "playful"
    concise = "concise"
    founder_style = "founder_style"


class SupportedEventType(StrEnum):
    push = "push"
    release = "release"
    pull_request_merged = "pull_request_merged"


class PrivateRepoMode(StrEnum):
    normal = "normal"
    conservative = "conservative"


class AchievementDigestFrequency(StrEnum):
    weekly = "weekly"
    monthly = "monthly"
