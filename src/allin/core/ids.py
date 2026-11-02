import re
import secrets
import string
from typing import NewType

_ALPHABET = string.ascii_lowercase + string.digits
_LENGTH = 6
# A human may choose one, or change it, within these lengths (#59).
SESSION_ID_LENGTHS = (4, 12)
_SESSION_ID = re.compile(
    f"[{re.escape(_ALPHABET)}]{{{SESSION_ID_LENGTHS[0]},{SESSION_ID_LENGTHS[1]}}}"
)

MainId = NewType("MainId", str)
# A Session's id, also its folder's name: made only by ``new_session_id`` and ``parse_session_id``.
SessionId = NewType("SessionId", str)
# A piece of a Profile, as ``<kind>:<name>`` (``function:clock``): made by ``PieceRef.id``.
PieceId = NewType("PieceId", str)
ROOT_MAIN_ID = MainId("root")  # the Main started from the command line; submains get their own


def new_session_id() -> SessionId:
    """Short random Session id: 6 characters of ``[a-z0-9]``."""
    return SessionId("".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH)))


def new_prompt_id() -> str:
    """A Prompt's id, the name of its attachments' folder: like a Session's, 6 characters."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(_LENGTH))


def parse_session_id(text: str) -> SessionId | None:
    """``text`` as a Session id if it has the shape of one, 4 to 12 characters of ``[a-z0-9]``
    (so it is safe as a folder name)."""
    return SessionId(text) if _SESSION_ID.fullmatch(text) is not None else None


# A Profile's name, also its folder's under ``profiles/``: letters, digits and hyphens, 3 to 32.
ProfileName = NewType("ProfileName", str)
_PROFILE_NAME = re.compile(r"[A-Za-z0-9-]{3,32}")
_GENERATED_PROFILE_LENGTH = 8
DEFAULT_PROFILE = ProfileName("default")  # what there was before Profiles becomes this one


def parse_profile_name(text: str) -> ProfileName | None:
    """``text`` as a Profile's name if it has the shape of one (so it is safe as a folder name)."""
    return ProfileName(text) if _PROFILE_NAME.fullmatch(text) is not None else None


def new_profile_name() -> ProfileName:
    """A Profile's name for ``/profile new`` without one: 8 characters of ``[a-z0-9]``."""
    return ProfileName("".join(secrets.choice(_ALPHABET) for _ in range(_GENERATED_PROFILE_LENGTH)))


# A Notice or Confirmation on screen, made by Main; its Window answers by it.
BoxId = NewType("BoxId", str)

# A Supervisor's name, also its files' in ``servers/``: a letter, then letters, digits, - or _.
SupervisorName = NewType("SupervisorName", str)
SUPERVISOR_NAME_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"
_SUPERVISOR_NAME = re.compile(SUPERVISOR_NAME_PATTERN)


def parse_supervisor_name(text: str) -> SupervisorName | None:
    """``text`` as a Supervisor's name if it has the shape of one (safe as a file name)."""
    return SupervisorName(text) if _SUPERVISOR_NAME.fullmatch(text) is not None else None


# The name of a Server in its slot locks and in Main's queue: its Supervisor's name or, for a
# Server Main only observes, its port, which never looks like a Supervisor's name.
ServerKey = NewType("ServerKey", str)
