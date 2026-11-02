"""The seccomp filter: what Landlock does not see (ADR-0016), as a hand-written classic BPF
program applied through ``ctypes`` (#99).

It refuses with EPERM: creating any socket (``socketpair`` is another call and stays allowed;
``@reach`` opens families in #101), io_uring, which makes sockets without passing through here,
and the ioctls that push keys into a terminal. Any other architecture, x32 included, is killed:
the call numbers below are x86_64's.
"""

import ctypes
import os
import struct

from allin.core.errors import ConfinementFailed

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

# Offsets in struct seccomp_data; arguments are read by their low 32 bits, which is what the
# kernel compares for ioctl's command (an unsigned int).
_NR = 0
_ARCH = 4
_ARG1_LOW = 24

type _Instruction = tuple[int, int, int, int]


def _ld(offset: int) -> _Instruction:
    return (_LD_W_ABS, 0, 0, offset)


def _jeq(value: int, if_true: int, if_false: int) -> _Instruction:
    return (_JEQ_K, if_true, if_false, value)


def _ret(value: int) -> _Instruction:
    return (_RET_K, 0, 0, value)


def program() -> bytes:
    """The filter as the kernel takes it; each jump counts the instructions it skips."""
    eperm = _RET_ERRNO | _EPERM
    code: list[_Instruction] = [
        _ld(_ARCH),
        _jeq(_AUDIT_ARCH_X86_64, 1, 0),
        _ret(_RET_KILL_PROCESS),
        _ld(_NR),
        (_JGE_K, 0, 1, _X32_SYSCALL_BIT),
        _ret(_RET_KILL_PROCESS),
    ]
    for number in (*_SYS_IO_URING, _SYS_SOCKET):
        code += [_jeq(number, 0, 1), _ret(eperm)]
    code += [
        _jeq(_SYS_IOCTL, 0, 4),
        _ld(_ARG1_LOW),
        _jeq(_TIOCSTI, 1, 0),
        _jeq(_TIOCLINUX, 0, 1),
        _ret(eperm),
        _ret(_RET_ALLOW),
    ]
    return b"".join(struct.pack("HBBI", *instruction) for instruction in code)


class _SockFprog(ctypes.Structure):
    _fields_ = (("len", ctypes.c_ushort), ("filter", ctypes.c_void_p))


def apply_filter() -> None:
    """Install the filter on every thread of this process; it needs no_new_privs, which
    ``landlock.restrict_self`` sets first."""
    code = program()
    buffer = ctypes.create_string_buffer(code, len(code))
    prog = _SockFprog(len(code) // 8, ctypes.cast(buffer, ctypes.c_void_p))
    if _libc.syscall(_SYS_SECCOMP, _SET_MODE_FILTER, _FILTER_FLAG_TSYNC, ctypes.byref(prog)) < 0:
        errno = ctypes.get_errno()
        raise ConfinementFailed(call="seccomp", errno=errno, reason=os.strerror(errno))
