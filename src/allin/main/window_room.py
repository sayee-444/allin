"""The Máximo de Ventanas (#52, ADR-0020): which Sessions leave the view so another fits.

Pure, so the rule is tested with fake Windows: what a human opens always shows, and makes room
by sending to the background the Session the human used least lately, never one waiting for a
Confirmation while another is left.
"""

from collections.abc import Container, Mapping, Sequence

from allin.core.ids import SessionId

NO_LIMIT = 0  # ``windows.max=0``: no limit


def displaced(
    visible: Sequence[SessionId],
    used: Mapping[SessionId, int],
    asking: Container[SessionId],
    maximum: int,
    coming: int = 1,
) -> list[SessionId]:
    """The visible Sessions that go to the background so ``coming`` more fit under
    ``maximum``: least used first (``used``: when each was last used, higher is later), those
    waiting for a Confirmation last."""
    if maximum == NO_LIMIT:
        return []
    excess = len(visible) + coming - maximum
    if excess <= 0:
        return []
    order = sorted(visible, key=lambda session_id: (session_id in asking, used.get(session_id, 0)))
    return order[:excess]


def fits(visible: int, maximum: int) -> bool:
    """Whether one more Window fits without sending any to the background."""
    return maximum == NO_LIMIT or visible < maximum
