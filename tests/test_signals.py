import os
import signal
import threading

import pytest

from allin.main.signals import SHUTDOWN_SIGNALS, ShutdownSignals


@pytest.mark.parametrize("signum", SHUTDOWN_SIGNALS, ids=lambda s: s.name)
def test_a_shutdown_signal_is_reported_instead_of_killing_main(signum: signal.Signals):
    received = threading.Event()
    signals = ShutdownSignals(received.set)
    signals.install()
    try:
        os.kill(os.getpid(), signum)
        assert received.wait(timeout=5)
    finally:
        signals.uninstall()


def test_uninstall_gives_the_signals_their_handlers_back():
    before = signal.getsignal(signal.SIGTERM)
    signals = ShutdownSignals(lambda: None)
    signals.install()
    signals.uninstall()
    assert signal.getsignal(signal.SIGTERM) == before
