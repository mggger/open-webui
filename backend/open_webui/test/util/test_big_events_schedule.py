from open_webui.utils import big_events


def test_scheduler_waits_until_exact_daily_due_time(monkeypatch):
    monkeypatch.setattr(big_events, "DISCOVERY_INTERVAL", 86400)
    monkeypatch.setattr(big_events, "_epoch_now", lambda: 1_000_000)

    state = {
        "lastSuccess": 1_000_000 - 23 * 60 * 60,
        "engine": big_events.DISCOVERY_ENGINE,
    }

    assert not big_events.discovery_is_stale(state)
    assert big_events.seconds_until_next_discovery(state) == 60 * 60


def test_scheduler_runs_immediately_without_previous_success(monkeypatch):
    monkeypatch.setattr(big_events, "_epoch_now", lambda: 1_000_000)

    state = {"lastSuccess": None}

    assert big_events.discovery_is_stale(state)
    assert big_events.seconds_until_next_discovery(state) == 1
