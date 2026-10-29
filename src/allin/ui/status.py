"""The status a Window shows on its Chat's border, kept free of curses so it can be tested."""


def context_status(context: int | None, limit: int, tokens_per_second: float | None) -> str:
    """The Context against the Session's limit and how fast its last Step generated."""
    used = "?" if context is None else str(context)
    speed = "?" if tokens_per_second is None else f"{tokens_per_second:.1f}"
    return f"context {used}/{limit} · {speed} tok/s"
