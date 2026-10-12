import pytest

from xrp_futures.execution.kill_switch import KillSwitch


@pytest.mark.parametrize("path, tripped", [
    ([250, 260, 240, 200], False),          # -23% from peak 260: still trading
    ([250, 240, 230, 180], False),          # -28% from peak 250: still trading
    ([250, 300, 250, 209], True),           # -30.3% from peak 300: blocked
])
def test_trips_at_30pct_from_peak(path, tripped):
    ks = KillSwitch(max_drawdown=0.30)
    assert [ks.update(e) for e in path][-1] is tripped


def test_latches_even_if_equity_recovers():
    ks = KillSwitch(max_drawdown=0.30)
    for e in [1000, 690]:
        ks.update(e)
    assert ks.tripped and ks.tripped_at_equity == 690
    assert ks.update(2000) is True     # recovery does NOT re-enable trading


def test_manual_reset_sets_new_peak():
    ks = KillSwitch(max_drawdown=0.30)
    ks.update(1000)
    ks.update(600)
    ks.reset(600)
    assert not ks.tripped and ks.peak_equity == 600
    assert ks.update(500) is False     # -16.7% from the new peak


def test_roundtrip_dict():
    ks = KillSwitch(0.30, peak_equity=300.0, tripped=True, tripped_at_equity=200.0)
    assert KillSwitch.from_dict(ks.to_dict()) == ks
