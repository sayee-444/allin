import re
import secrets
import string
from typing import NewType

_ALPHABET = string.ascii_lowercase + string.digits
_LENGTH = 6
_SESSION_ID = re.compile(f"[{re.escape(_ALPHABET)}]{{{_LENGTH}}}")

MainId = NewType("MainId", str)
# A Session's id, also its folder's name: made only by ``new_session_id`` and ``parse_session_id``.
SessionId = NewType("SessionId", str)
ROOT_MAIN_ID = MainId("root")  # the Main started from the command line; submains get their own


def new_session_id() -> SessionId:
    """Short random Session id: 6 characters of ``[a-z0-9]``."""
    return SessionId("".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH)))


def parse_session_id(text: str) -> SessionId | None:
    """``text`` as a Session id if it has the shape of one (so it is safe as a folder name)."""
    return SessionId(text) if _SESSION_ID.fullmatch(text) is not None else None
