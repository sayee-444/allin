"""Origins for tests (ADR-0013)."""

from allin.core.origin import Link, Origin

# Some Session, as an Origin was saved before its chain (#105): no Log of its own to keep.
A_SESSION = Origin(chain=(Link("session"),))
