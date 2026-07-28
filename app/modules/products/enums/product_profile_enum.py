from enum import StrEnum


class ProductProfileStatus(StrEnum):
    draft = "draft"
    approved = "approved"
    superseded = "superseded"


class ProductProfileAuditAction(StrEnum):
    draft_created = "draft_created"
    draft_updated = "draft_updated"
    profile_approved = "profile_approved"
    profile_superseded = "profile_superseded"


class ProductProfileConversionGoal(StrEnum):
    awareness = "awareness"
    waitlist = "waitlist"
    signup = "signup"
    trial = "trial"
    demo = "demo"
    purchase = "purchase"
    contact = "contact"
    newsletter = "newsletter"
    retention = "retention"
