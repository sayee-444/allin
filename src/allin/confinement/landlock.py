"""Landlock through ``ctypes``: three system calls and fixed structures, no library (#99).

A process asks the kernel to restrict itself and every child it will have; it cannot be undone.
"""

import ctypes
import os
from dataclasses import dataclass
from pathlib import Path

from allin.core.errors import ConfinementFailed

_libc = ctypes.CDLL(None, use_errno=True)
_libc.syscall.restype = ctypes.c_long
_libc.prctl.restype = ctypes.c_int

# x86_64 system call numbers.
_CREATE_RULESET = 444
_ADD_RULE = 445
_RESTRICT_SELF = 446

_CREATE_RULESET_VERSION = 1 << 0
_RULE_PATH_BENEATH = 1
_RESTRICT_SELF_TSYNC = 1 << 3  # ABI 8: every thread, not only the caller

_FS_EXECUTE = 1 << 0
_FS_WRITE_FILE = 1 << 1
_FS_READ_FILE = 1 << 2
_FS_READ_DIR = 1 << 3
_FS_REMOVE_DIR = 1 << 4
_FS_REMOVE_FILE = 1 << 5
_FS_MAKE_CHAR = 1 << 6
_FS_MAKE_DIR = 1 << 7
_FS_MAKE_REG = 1 << 8
_FS_MAKE_SOCK = 1 << 9
_FS_MAKE_FIFO = 1 << 10
_FS_MAKE_BLOCK = 1 << 11
_FS_MAKE_SYM = 1 << 12
_FS_REFER = 1 << 13  # ABI 2
_FS_TRUNCATE = 1 << 14  # ABI 3
_FS_IOCTL_DEV = 1 << 15  # ABI 5
_NET_BIND_TCP = 1 << 0  # ABI 4
_NET_CONNECT_TCP = 1 << 1
_SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0  # ABI 6
_SCOPE_SIGNAL = 1 << 1

_READ = _FS_EXECUTE | _FS_READ_FILE | _FS_READ_DIR
_WRITE = (
    _FS_WRITE_FILE
    | _FS_REMOVE_DIR
    | _FS_REMOVE_FILE
    | _FS_MAKE_DIR
    | _FS_MAKE_REG
    | _FS_MAKE_SYM
    | _FS_MAKE_FIFO
    | _FS_REFER
    | _FS_TRUNCATE
)
# Rights a rule on a regular file cannot carry: the kernel refuses them (EINVAL).
_DIRECTORY_ONLY = (
    _FS_READ_DIR
    | _FS_REMOVE_DIR
    | _FS_REMOVE_FILE
    | _FS_MAKE_CHAR
    | _FS_MAKE_DIR
    | _FS_MAKE_REG
    | _FS_MAKE_SOCK
    | _FS_MAKE_FIFO
    | _FS_MAKE_BLOCK
    | _FS_MAKE_SYM
    | _FS_REFER
)

_PR_SET_NO_NEW_PRIVS = 38

NETWORK_ABI = 4  # TCP bind and connect by port
SCOPES_ABI = 6  # signals and abstract unix sockets outside the domain


def abi_version() -> int:
    """The kernel's Landlock ABI; 0 if it has none or it is turned off."""
    return max(_libc.syscall(_CREATE_RULESET, None, 0, _CREATE_RULESET_VERSION), 0)


def _handled_fs(abi: int) -> int:
    handled = (1 << 13) - 1  # ABI 1
    if abi >= 2:
        handled |= _FS_REFER
    if abi >= 3:
        handled |= _FS_TRUNCATE
    if abi >= 5:
        handled |= _FS_IOCTL_DEV
    return handled


class _RulesetAttr(ctypes.Structure):
    _fields_ = (
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    )


class _PathBeneathAttr(ctypes.Structure):
    # Packed, 12 bytes: the kernel's __attribute__((packed)); the layouts agree on it.
    _layout_ = "ms"
    _pack_ = 1
    _fields_ = (("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32))


@dataclass(frozen=True)
class Restricted:
    """What the restriction covered on this kernel."""

    abi: int
    signals_scoped: bool  # kill(-1) reaches only the processes of this domain


def restrict_self(read: tuple[Path, ...], write: tuple[Path, ...]) -> Restricted:
    """Restrict this process and its future children to reading ``read`` and reading and
    writing ``write``, with no TCP and, from ABI 6, no signal or abstract unix socket outside
    the domain. Paths that do not exist are skipped. What the kernel lacks is left out."""
    abi = abi_version()
    _no_new_privs()
    if abi == 0:
        return Restricted(abi=0, signals_scoped=False)
    handled_fs = _handled_fs(abi)
    attr = _RulesetAttr(
        handled_fs,
        _NET_BIND_TCP | _NET_CONNECT_TCP if abi >= NETWORK_ABI else 0,
        _SCOPE_ABSTRACT_UNIX_SOCKET | _SCOPE_SIGNAL if abi >= SCOPES_ABI else 0,
    )
    ruleset = checked(
        _libc.syscall(_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), 0),
        "landlock_create_ruleset",
    )
    try:
        for path in read:
            _allow(ruleset, path, _READ & handled_fs)
        for path in write:
            _allow(ruleset, path, (_READ | _WRITE) & handled_fs)
        flags = _RESTRICT_SELF_TSYNC if abi >= 8 else 0
        checked(_libc.syscall(_RESTRICT_SELF, ruleset, flags), "landlock_restrict_self")
    finally:
        os.close(ruleset)
    return Restricted(abi=abi, signals_scoped=abi >= SCOPES_ABI)


def _allow(ruleset: int, path: Path, access: int) -> None:
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except FileNotFoundError:
        return  # nothing there to grant
    try:
        if not path.is_dir():
            access &= ~_DIRECTORY_ONLY
        attr = _PathBeneathAttr(access, fd)
        checked(
            _libc.syscall(_ADD_RULE, ruleset, _RULE_PATH_BENEATH, ctypes.byref(attr), 0),
            "landlock_add_rule",
        )
    finally:
        os.close(fd)


def _no_new_privs() -> None:
    checked(_libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "PR_SET_NO_NEW_PRIVS")


def checked(result: int, call: str) -> int:
    """``result`` of the system call ``call``, or ``ConfinementFailed`` with its errno."""
    if result < 0:
        raise ConfinementFailed(call=call, errno=ctypes.get_errno())
    return result
