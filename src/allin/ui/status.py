"""The status a Window shows on its Chat's border, kept free of curses so it can be tested."""


def context_status(context: int | None, limit: int | None, tokens_per_second: float | None) -> str:
    """The Context against the Server's limit and how fast its last Step generated."""
    used = "?" if context is None else str(context)
    speed = "?" if tokens_per_second is None else f"{tokens_per_second:.1f}"
    return f"context {used} / {_tokens(limit)} · {speed} tok/s"


def _tokens(limit: int | None) -> str:
    """A context limit as llama-server's ``-c`` is usually written: 64000 is ``64k``."""
    if limit is None:
        return "?"
    if limit >= 1000:
        return f"{round(limit / 1000)}k"
    return str(limit)
