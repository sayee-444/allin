import queue
import subprocess
import sys
from pathlib import Path

from allin.main.events import MainEvent, SlotFreed
from allin.main.slot_locks import SlotLocks, SlotWaiter


def test_each_step_takes_a_free_slot_until_there_is_none(tmp_path: Path):
    locks = SlotLocks(tmp_path / "servers", "9931")
    first, second = locks.take(2), locks.take(2)
    assert first is not None and second is not None
    assert {first.slot, second.slot} == {0, 1}
    assert locks.take(2) is None
    assert (tmp_path / "servers" / "9931.slot0.lock").exists()


def test_a_released_slot_can_be_taken_again(tmp_path: Path):
    locks = SlotLocks(tmp_path, "9931")
    lock = locks.take(1)
    assert lock is not None
    lock.release()
    lock.release()  # releasing twice does nothing
    assert locks.take(1) is not None


def test_two_mains_on_the_same_server_never_hold_the_same_slot(tmp_path: Path):
    main_a, main_b = SlotLocks(tmp_path, "9931"), SlotLocks(tmp_path, "9931")
    lock = main_a.take(1)
    assert lock is not None
    assert main_b.take(1) is None
    lock.release()
    assert main_b.take(1) is not None


def test_slots_of_other_servers_are_not_shared(tmp_path: Path):
    assert SlotLocks(tmp_path, "9931").take(1) is not None
    assert SlotLocks(tmp_path, "9932").take(1) is not None


def test_a_main_that_dies_mid_step_leaves_its_slot_free(tmp_path: Path):
    script = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from allin.main.slot_locks import SlotLocks\n"
        "lock = SlotLocks(Path(sys.argv[1]), '9931').take(1)\n"
        "print(lock.slot, flush=True)\n"
        "time.sleep(60)\n"
    )
    dying = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path)], stdout=subprocess.PIPE, text=True
    )
    try:
        assert dying.stdout is not None and dying.stdout.readline() == "0\n"
        assert SlotLocks(tmp_path, "9931").take(1) is None
    finally:
        dying.kill()
        dying.wait()
    assert SlotLocks(tmp_path, "9931").take(1) is not None


def test_the_waiter_says_once_when_a_slot_another_main_held_is_free(tmp_path: Path):
    other = SlotLocks(tmp_path, "9931").take(1)
    assert other is not None
    inbox: queue.Queue[MainEvent] = queue.Queue()
    waiter = SlotWaiter(SlotLocks(tmp_path, "9931"), inbox, interval=0.01)
    waiter.start(1)
    waiter.start(1)  # already waiting: still one wait, one event
    try:
        assert inbox.empty()
        other.release()
        assert inbox.get(timeout=5) == SlotFreed()
        waiter.start(1)  # a new wait once the last one reported
        assert inbox.get(timeout=5) == SlotFreed()
    finally:
        waiter.stop()
    assert inbox.empty()


def test_a_waiter_with_every_slot_held_waits_until_main_stops(tmp_path: Path):
    assert SlotLocks(tmp_path, "9931").take(1) is not None
    inbox: queue.Queue[MainEvent] = queue.Queue()
    waiter = SlotWaiter(SlotLocks(tmp_path, "9931"), inbox, interval=0.01)
    waiter.start(1)
    waiter.stop()
    assert inbox.empty()
