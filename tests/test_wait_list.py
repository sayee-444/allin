import os
import subprocess
import sys
from pathlib import Path

from allin.core.ids import MainId, ServerKey, SessionId
from allin.core.wait_list import Reservation, Waiter, WaitList

KEY = ServerKey("9931")
A, B = MainId("root"), MainId("other")


def waiter(main: MainId, session: str, since: float, need: int = 100, pid: int = 0) -> Waiter:
    return Waiter(
        main=main,
        pid=pid or os.getpid(),
        session=SessionId(session),
        kind="prompt",
        since=since,
        need=need,
        firm_at=since + 60,
    )


def reservation(main: MainId, session: str, tokens: int, pid: int = 0) -> Reservation:
    return Reservation(main=main, pid=pid or os.getpid(), session=SessionId(session), tokens=tokens)


def dead_pid() -> int:
    """The pid of a process that already ended."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_an_empty_list_has_nobody(tmp_path: Path) -> None:
    seen = WaitList(tmp_path, KEY).look()
    assert seen.waiting == ()
    assert seen.reserved == ()


def test_each_main_publishes_its_own_waiters_without_touching_the_others(tmp_path: Path) -> None:
    waits = WaitList(tmp_path, KEY)
    waits.publish(A, os.getpid(), [waiter(A, "s1", 1.0)])
    waits.publish(B, os.getpid(), [waiter(B, "s2", 2.0)])
    waits.publish(A, os.getpid(), [waiter(A, "s3", 3.0)])
    assert [w.session for w in waits.look().waiting] == ["s2", "s3"]


def test_publishing_again_keeps_when_a_session_started_waiting(tmp_path: Path) -> None:
    waits = WaitList(tmp_path, KEY)
    waits.publish(A, os.getpid(), [waiter(A, "s1", 1.0)])
    waits.publish(A, os.getpid(), [waiter(A, "s1", 5.0, need=300)])
    (kept,) = waits.look().waiting
    assert kept.since == 1.0
    assert kept.firm_at == 61.0
    assert kept.need == 300  # what it needs now


def test_another_main_sees_the_same_list(tmp_path: Path) -> None:
    WaitList(tmp_path, KEY).publish(A, os.getpid(), [waiter(A, "s1", 1.0)])
    assert [w.session for w in WaitList(tmp_path, KEY).look().waiting] == ["s1"]


def test_reservations_are_added_and_released_by_session(tmp_path: Path) -> None:
    waits = WaitList(tmp_path, KEY)
    waits.reserve(reservation(A, "s1", 500))
    waits.reserve(reservation(B, "s2", 700))
    waits.release(A, os.getpid(), SessionId("s1"))
    assert waits.look().reserved == (reservation(B, "s2", 700),)


def test_reserving_takes_the_session_out_of_the_waiters(tmp_path: Path) -> None:
    waits = WaitList(tmp_path, KEY)
    waits.publish(A, os.getpid(), [waiter(A, "s1", 1.0), waiter(A, "s2", 2.0)])
    waits.reserve(reservation(A, "s1", 500))
    assert [w.session for w in waits.look().waiting] == ["s2"]


def test_what_a_dead_main_left_is_dropped(tmp_path: Path) -> None:
    gone = dead_pid()
    waits = WaitList(tmp_path, KEY)
    waits.publish(B, os.getpid(), [waiter(B, "s2", 2.0, pid=gone)])
    waits.reserve(reservation(B, "s3", 900, pid=gone))
    waits.publish(A, os.getpid(), [waiter(A, "s1", 3.0)])
    seen = waits.look()
    assert [w.session for w in seen.waiting] == ["s1"]
    assert seen.reserved == ()


def test_every_change_moves_the_version(tmp_path: Path) -> None:
    waits = WaitList(tmp_path, KEY)
    first = waits.version()
    waits.publish(A, os.getpid(), [waiter(A, "s1", 1.0)])
    second = waits.version()
    waits.release(A, os.getpid(), SessionId("nobody"))  # nothing changed
    assert first < second == waits.version()


def test_an_unreadable_file_counts_as_empty(tmp_path: Path) -> None:
    waits = WaitList(tmp_path, KEY)
    waits.publish(A, os.getpid(), [waiter(A, "s1", 1.0)])
    (tmp_path / f"{KEY}.waits.json").write_text("not json")
    assert waits.look().waiting == ()
    waits.publish(A, os.getpid(), [waiter(A, "s2", 2.0)])
    assert [w.session for w in waits.look().waiting] == ["s2"]


def test_two_mains_with_the_same_id_keep_their_own_waiters(tmp_path: Path) -> None:
    # Two root Mains on different data folders that share a Server's folder.
    waits = WaitList(tmp_path, KEY)
    other_pid = os.getppid()  # a live process that is not this one
    waits.publish(A, other_pid, [waiter(A, "s2", 2.0, pid=other_pid)])
    waits.publish(A, os.getpid(), [waiter(A, "s1", 1.0)])
    waits.publish(A, os.getpid(), [])
    assert [w.session for w in waits.look().waiting] == ["s2"]
    waits.reserve(reservation(A, "s3", 10, pid=other_pid))
    waits.release(A, os.getpid(), SessionId("s3"))
    assert [r.session for r in waits.look().reserved] == ["s3"]
