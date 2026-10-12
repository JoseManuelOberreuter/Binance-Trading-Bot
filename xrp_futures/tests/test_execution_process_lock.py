import pytest

from xrp_futures.execution.process_lock import AlreadyRunning, ProcessLock


def test_second_bot_on_same_environment_is_refused(tmp_path):
    first = ProcessLock(tmp_path / "bot.lock").acquire()
    with pytest.raises(AlreadyRunning):
        ProcessLock(tmp_path / "bot.lock").acquire()
    first.release()
    ProcessLock(tmp_path / "bot.lock").acquire().release()      # free again once released


def test_environments_have_independent_locks(tmp_path):
    a = ProcessLock(tmp_path / "testnet" / "bot.lock").acquire()
    b = ProcessLock(tmp_path / "production" / "bot.lock").acquire()
    a.release(), b.release()
