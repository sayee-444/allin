"""Marks the Windows and Main's Chat show on their border."""

DEVASTATION_MARK = "--devastation: Main answers its Confirmations"


def unconfined_mark(missing: tuple[str, ...]) -> str:
    """The permanent mark of a kernel that cannot confine tools in full (ADR-0016)."""
    return f"tools not fully confined: no {', '.join(missing)}" if missing else ""


def border_mark(devastation: bool, missing: tuple[str, ...]) -> str:
    """Every mark a border shows before its title, in a fixed order."""
    parts = [DEVASTATION_MARK if devastation else "", unconfined_mark(missing)]
    return " · ".join(part for part in parts if part)
