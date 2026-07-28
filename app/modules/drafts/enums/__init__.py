from enum import StrEnum


class DraftStatus(StrEnum):
    pending = "pending"
    approved = "approved"
    posted = "posted"
    rejected = "rejected"


__all__ = ["DraftStatus"]
