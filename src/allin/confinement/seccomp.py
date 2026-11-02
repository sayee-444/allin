"""The seccomp filter: what Landlock does not see (ADR-0016), as a hand-written classic BPF
program applied through ``ctypes`` (#99).

``socket`` creates only the families a call's Alcance allows: IP and the netlink route socket
(glibc's ``getaddrinfo`` asks it which addresses exist) with network, unix sockets with
``display``, which lets the tool reach every unix socket of the user's session (ADR-0016).
Anything else is refused with EPERM; ``socketpair`` is another call and stays allowed. So are
io_uring, which makes sockets without passing through here, and the ioctls that push keys
into a terminal. Any other architecture, x32 included, is killed: the call numbers below are
x86_64's.
"""

import ctypes
import struct

from allin.confinement.landlock import checked

_libc = ctypes.CDLL(None, use_errno=True)
_libc.syscall.restype = ctypes.c_long

_SYS_SECCOMP = 317
_SET_MODE_FILTER = 1
_FILTER_FLAG_TSYNC = 1

_AUDIT_ARCH_X86_64 = 0xC000003E
_X32_SYSCALL_BIT = 0x40000000
_RET_KILL_PROCESS = 0x80000000
_RET_ERRNO = 0x00050000
_RET_ALLOW = 0x7FFF0000
_EPERM = 1

_LD_W_ABS = 0x20
_JEQ_K = 0x15
_JGE_K = 0x35
_RET_K = 0x06

_SYS_IOCTL = 16
_SYS_SOCKET = 41
_SYS_IO_URING = (425, 426, 427)  # setup, enter, register
_TIOCSTI = 0x5412
_TIOCLINUX = 0x541C
_AF_UNIX = 1
_AF_INET = 2
_AF_INET6 = 10
_AF_NETLINK = 16
_NETLINK_ROUTE = 0

# Offsets in struct seccomp_data; arguments are read by their low 32 bits, which is what the
# kernel compares for socket's family and protocol and for ioctl's command (all ints).
_NR = 0
_ARCH = 4
_ARG0_LOW = 16
_ARG1_LOW = 24
_ARG2_LOW = 32

type _Instruction = tuple[int, int, int, int]
type _Target = str | None  # a label further down, or None for the next instruction
type _Line = tuple[int, int, _Target, _Target] | tuple[str]  # a jump or a statement; a label


def _ld(offset: int) -> _Line:
    return (_LD_W_ABS, offset, None, None)


def _jeq(value: int, if_true: _Target, if_false: _Target = None) -> _Line:
    return (_JEQ_K, value, if_true, if_false)


def _ret(value: int) -> _Line:
    return (_RET_K, value, None, None)


def program(*, ip: bool, unix: bool) -> bytes:
    """The filter as the kernel takes it: IP sockets only with ``ip``, unix ones only with
    ``unix``."""
    families = [*((_AF_INET, _AF_INET6) if ip else ()), *((_AF_UNIX,) if unix else ())]
    lines: list[_Line] = [
        _ld(_ARCH),
        _jeq(_AUDIT_ARCH_X86_64, None, "kill"),
        _ld(_NR),
        (_JGE_K, _X32_SYSCALL_BIT, "kill", None),
        *(_jeq(number, "deny") for number in _SYS_IO_URING),
        _jeq(_SYS_SOCKET, None, "ioctl"),
        _ld(_ARG0_LOW),
        *(_jeq(family, "allow") for family in families),
    ]
    if ip:
        lines += [_jeq(_AF_NETLINK, None, "deny"), _ld(_ARG2_LOW), _jeq(_NETLINK_ROUTE, "allow")]
    lines += [
        _ret(_RET_ERRNO | _EPERM),
        ("ioctl",),
        _jeq(_SYS_IOCTL, None, "allow"),
        _ld(_ARG1_LOW),
        _jeq(_TIOCSTI, "deny"),
        _jeq(_TIOCLINUX, "deny"),
        ("allow",),
        _ret(_RET_ALLOW),
        ("deny",),
        _ret(_RET_ERRNO | _EPERM),
        ("kill",),
        _ret(_RET_KILL_PROCESS),
    ]
    return b"".join(struct.pack("HBBI", *instruction) for instruction in _assemble(lines))


def _assemble(lines: list[_Line]) -> list[_Instruction]:
    """Each jump's targets become the number of instructions it skips, as BPF counts them."""
    at: dict[str, int] = {}
    code: list[tuple[int, int, _Target, _Target]] = []
    for line in lines:
        if len(line) == 1:
            at[line[0]] = len(code)
        else:
            code.append(line)

    def skip(target: _Target, here: int) -> int:
        return 0 if target is None else at[target] - here - 1

    return [(op, skip(yes, i), skip(no, i), k) for i, (op, k, yes, no) in enumerate(code)]


class _SockFprog(ctypes.Structure):
    _fields_ = (("len", ctypes.c_ushort), ("filter", ctypes.c_void_p))


def apply_filter(*, ip: bool, unix: bool) -> None:
    """Install the filter on every thread of this process; it needs no_new_privs, which
    ``landlock.restrict_self`` sets first."""
    code = program(ip=ip, unix=unix)
    buffer = ctypes.create_string_buffer(code, len(code))
    prog = _SockFprog(len(code) // 8, ctypes.cast(buffer, ctypes.c_void_p))
    checked(
        _libc.syscall(_SYS_SECCOMP, _SET_MODE_FILTER, _FILTER_FLAG_TSYNC, ctypes.byref(prog)),
        "seccomp",
    )
