from enum import StrEnum


class WorkspaceRole(StrEnum):
    owner = "owner"
    admin = "admin"
    editor = "editor"
    reviewer = "reviewer"
    viewer = "viewer"
