"""Throwaway prototype for #99: Landlock and seccomp from Python with ctypes only.

Not part of AllIn. It answers what docs/research/encierro-prototipo.md reports; #100 writes
the real launcher in src/.
"""

import ctypes
import os
import struct
from dataclasses import dataclass, field
from typing import Literal

_libc = ctypes.CDLL(None, use_errno=True)
_libc.syscall.restype = ctypes.c_long
_libc.prctl.restype = ctypes.c_int

# x86_64 syscall numbers.
SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446
SYS_SECCOMP = 317

LANDLOCK_CREATE_RULESET_VERSION = 1 << 0
LANDLOCK_RULE_PATH_BENEATH = 1
LANDLOCK_RULE_NET_PORT = 2

FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13  # ABI 2
FS_TRUNCATE = 1 << 14  # ABI 3
FS_IOCTL_DEV = 1 << 15  # ABI 5

NET_BIND_TCP = 1 << 0  # ABI 4
NET_CONNECT_TCP = 1 << 1

SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0  # ABI 6
SCOPE_SIGNAL = 1 << 1

FS_READ = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
FS_WRITE = (
    FS_WRITE_FILE
    | FS_REMOVE_DIR
    | FS_REMOVE_FILE
    | FS_MAKE_DIR
    | FS_MAKE_REG
    | FS_MAKE_SYM
    | FS_MAKE_FIFO
    | FS_REFER
    | FS_TRUNCATE
)
FS_ALL = (1 << 16) - 1
# Rights that only make sense on directories; a rule on a regular file must drop them.
FS_DIR_ONLY = FS_ALL & ~(FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_TRUNCATE | FS_IOCTL_DEV)

PR_SET_PDEATHSIG = 1
PR_SET_SECCOMP = 22
PR_SET_CHILD_SUBREAPER = 36
PR_SET_NO_NEW_PRIVS = 38


class LandlockError(OSError):
    pass


def _check(result: int, what: str) -> int:
    if result < 0:
        err = ctypes.get_errno()
        raise LandlockError(err, f"{what}: {os.strerror(err)}")
    return result


def abi_version() -> int:
    return _libc.syscall(SYS_LANDLOCK_CREATE_RULESET, None, 0, LANDLOCK_CREATE_RULESET_VERSION)


class _RulesetAttr(ctypes.Structure):
    _fields_ = (
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    )


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = (("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32))


class _NetPortAttr(ctypes.Structure):
    _fields_ = (("allowed_access", ctypes.c_uint64), ("port", ctypes.c_uint64))


def create_ruleset(*, fs: int = FS_ALL, net: int = 0, scoped: int = 0) -> int:
    attr = _RulesetAttr(fs, net, scoped)
    return _check(
        _libc.syscall(
            SYS_LANDLOCK_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), 0
        ),
        "landlock_create_ruleset",
    )


def add_path(ruleset: int, path: str, access: int) -> None:
    fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        if not os.path.isdir(path):
            access &= ~FS_DIR_ONLY
        attr = _PathBeneathAttr(access, fd)
        _check(
            _libc.syscall(
                SYS_LANDLOCK_ADD_RULE, ruleset, LANDLOCK_RULE_PATH_BENEATH, ctypes.byref(attr), 0
            ),
            f"landlock_add_rule({path})",
        )
    finally:
        os.close(fd)


def add_port(ruleset: int, port: int, access: int = NET_CONNECT_TCP | NET_BIND_TCP) -> None:
    attr = _NetPortAttr(access, port)
    _check(
        _libc.syscall(SYS_LANDLOCK_ADD_RULE, ruleset, LANDLOCK_RULE_NET_PORT, ctypes.byref(attr), 0),
        f"landlock_add_rule(port {port})",
    )


def add_all_ports_but(ruleset: int, excluded: frozenset[int]) -> None:
    attr = _NetPortAttr(NET_CONNECT_TCP | NET_BIND_TCP, 0)
    ref = ctypes.byref(attr)
    syscall = _libc.syscall
    for port in range(65536):
        if port in excluded:
            continue
        attr.port = port
        if syscall(SYS_LANDLOCK_ADD_RULE, ruleset, LANDLOCK_RULE_NET_PORT, ref, 0) < 0:
            _check(-1, f"landlock_add_rule(port {port})")


def no_new_privs() -> None:
    _check(_libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "PR_SET_NO_NEW_PRIVS")


LANDLOCK_RESTRICT_SELF_TSYNC = 1 << 3  # ABI 8: confine every thread, not just the caller


def restrict_self(ruleset: int) -> None:
    flags = LANDLOCK_RESTRICT_SELF_TSYNC if abi_version() >= 8 else 0
    _check(_libc.syscall(SYS_LANDLOCK_RESTRICT_SELF, ruleset, flags), "landlock_restrict_self")


# --- seccomp: a hand-written classic BPF filter ---------------------------------------------

AUDIT_ARCH_X86_64 = 0xC000003E
X32_SYSCALL_BIT = 0x40000000
SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_ALLOW = 0x7FFF0000
SECCOMP_SET_MODE_FILTER = 1
SECCOMP_FILTER_FLAG_TSYNC = 1

BPF_LD_W_ABS = 0x20
BPF_JEQ_K = 0x15
BPF_JGE_K = 0x35
BPF_RET_K = 0x06

SYS_IOCTL = 16
SYS_SOCKET = 41
SYS_IO_URING_SETUP = 425
SYS_IO_URING_ENTER = 426
SYS_IO_URING_REGISTER = 427
TIOCSTI = 0x5412
TIOCLINUX = 0x541C
AF_UNIX = 1
AF_INET = 2
AF_INET6 = 10
EPERM = 1

_OFF_NR = 0
_OFF_ARCH = 4
_OFF_ARG0_LO = 16
_OFF_ARG1_LO = 24


@dataclass
class _Bpf:
    code: list[tuple[int, int, int, int]] = field(default_factory=list[tuple[int, int, int, int]])

    def ld(self, offset: int) -> None:
        self.code.append((BPF_LD_W_ABS, 0, 0, offset))

    def jeq(self, k: int, jt: int, jf: int) -> None:
        self.code.append((BPF_JEQ_K, jt, jf, k))

    def jge(self, k: int, jt: int, jf: int) -> None:
        self.code.append((BPF_JGE_K, jt, jf, k))

    def ret(self, k: int) -> None:
        self.code.append((BPF_RET_K, 0, 0, k))


def build_filter(*, net: bool, unix: bool) -> bytes:
    """Deny io_uring, TIOCSTI/TIOCLINUX, AF_UNIX sockets (unless `unix`) and IP sockets
    (unless `net`); socketpair is a different syscall and stays allowed."""
    eperm = SECCOMP_RET_ERRNO | EPERM
    p = _Bpf()
    p.ld(_OFF_ARCH)
    p.jeq(AUDIT_ARCH_X86_64, 1, 0)
    p.ret(SECCOMP_RET_KILL_PROCESS)
    p.ld(_OFF_NR)
    p.jge(X32_SYSCALL_BIT, 0, 1)
    p.ret(SECCOMP_RET_KILL_PROCESS)
    for nr in (SYS_IO_URING_SETUP, SYS_IO_URING_ENTER, SYS_IO_URING_REGISTER):
        p.jeq(nr, 0, 1)
        p.ret(eperm)
    # ioctl(fd, cmd): the kernel compares cmd as unsigned int, the low 32 bits.
    p.jeq(SYS_IOCTL, 0, 4)
    p.ld(_OFF_ARG1_LO)
    p.jeq(TIOCSTI, 1, 0)
    p.jeq(TIOCLINUX, 0, 1)
    p.ret(eperm)
    p.ld(_OFF_NR)
    denied_families = ([] if unix else [AF_UNIX]) + ([] if net else [AF_INET, AF_INET6])
    if denied_families:
        p.jeq(SYS_SOCKET, 0, 1 + 2 * len(denied_families))
        p.ld(_OFF_ARG0_LO)
        for family in denied_families:
            p.jeq(family, 0, 1)
            p.ret(eperm)
    p.ret(SECCOMP_RET_ALLOW)
    return b"".join(struct.pack("HBBI", *ins) for ins in p.code)


class _SockFprog(ctypes.Structure):
    _fields_ = (("len", ctypes.c_ushort), ("filter", ctypes.c_void_p))


def apply_filter(program: bytes) -> None:
    buf = ctypes.create_string_buffer(program, len(program))
    prog = _SockFprog(len(program) // 8, ctypes.cast(buf, ctypes.c_void_p))
    _check(
        _libc.syscall(SYS_SECCOMP, SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_TSYNC, ctypes.byref(prog)),
        "seccomp",
    )


# --- one call's confinement ------------------------------------------------------------------


@dataclass(frozen=True)
class Reach:
    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()
    # None: no network. A tuple: those TCP ports. "all": every port but `server_ports`.
    net: tuple[int, ...] | Literal["all"] | None = None
    server_ports: frozenset[int] = frozenset({9931})
    display: bool = False


def confine(reach: Reach) -> None:
    """Confine the calling process for good: Landlock (fs, TCP, scopes) and seccomp."""
    abi = abi_version()
    if abi < 6:
        raise LandlockError(0, f"Landlock ABI {abi} < 6")
    no_new_privs()
    ruleset = create_ruleset(
        fs=FS_ALL,
        net=NET_BIND_TCP | NET_CONNECT_TCP,
        scoped=SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL,
    )
    try:
        for path in reach.read:
            add_path(ruleset, path, FS_READ)
        for path in reach.write:
            add_path(ruleset, path, FS_READ | FS_WRITE)
        if reach.net == "all":
            add_all_ports_but(ruleset, reach.server_ports)
        elif isinstance(reach.net, tuple):
            for port in reach.net:
                add_port(ruleset, port)
        restrict_self(ruleset)
    finally:
        os.close(ruleset)
    apply_filter(build_filter(net=reach.net is not None, unix=reach.display))
