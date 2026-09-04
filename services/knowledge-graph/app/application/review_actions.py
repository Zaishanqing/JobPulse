"""Shared API/read-model projection of the existing review lifecycle."""


def allowed_review_actions(status: str) -> tuple[str, ...]:
    return {
        "pending": ("claim",),
        "claimed": ("approve", "reject", "modify"),
        "modified": ("approve", "reject"),
    }.get(status, ())
