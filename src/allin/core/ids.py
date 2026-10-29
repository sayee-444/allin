import secrets
import string

_ALPHABET = string.ascii_lowercase + string.digits


def new_session_id() -> str:
    """Short random Session id: 6 characters of ``[a-z0-9]``."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(6))
