import re
import secrets
import string
from typing import NewType

_ALPHABET = string.ascii_lowercase + string.digits
_LENGTH = 6
_SESSION_ID = re.compile(f"[{re.escape(_ALPHABET)}]{{{_LENGTH}}}")

MainId = NewType("MainId", str)
ROOT_MAIN_ID = MainId("root")  # the Main started from the command line; submains get their own


def new_session_id() -> str:
    """Short random Session id: 6 characters of ``[a-z0-9]``."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))


def is_session_id(text: str) -> bool:
    """Whether ``text`` has the shape of a Session id (so it is safe as a folder name)."""
    return _SESSION_ID.fullmatch(text) is not None
