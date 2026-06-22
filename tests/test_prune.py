"""Unit tests for the pure prune selector (no I/O, injected clock)."""
from datetime import datetime, timedelta, timezone

from packtrack.prune import select_prunable

NOW = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


def _ts(days_ago):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rec(num="X", status=None, monitor=True, days_ago=0, change_at="set"):
    rec = {"tracking_number": num, "last_status": status, "monitor": monitor}
    if change_at == "set":
        rec["last_change_at"] = _ts(days_ago)
    elif change_at is not None:
        rec["last_change_at"] = change_at  # explicit / malformed value
    return rec  # change_at None -> field omitted


def _prune(records, *, delivered_days=3, stale_days=30):
    return select_prunable(records, NOW, delivered_days=delivered_days, stale_days=stale_days)


def test_delivered_within_grace_kept():
    backfill, prune = _prune([_rec(status="delivered", days_ago=2)])
    assert prune == [] and backfill == []


def test_delivered_past_grace_pruned():
    rec = _rec(num="D", status="delivered", days_ago=4)
    backfill, prune = _prune([rec])
    assert prune == [rec]


def test_delivered_past_grace_pruned_even_if_monitor_flag_left_on():
    rec = _rec(num="D", status="delivered", monitor=True, days_ago=4)
    _, prune = _prune([rec])
    assert prune == [rec]


def test_undelivered_monitored_within_window_kept():
    _, prune = _prune([_rec(status="in_transit", days_ago=10)])
    assert prune == []


def test_undelivered_monitored_stale_pruned():
    rec = _rec(num="S", status="in_transit", days_ago=31)
    _, prune = _prune([rec])
    assert prune == [rec]


def test_undelivered_monitor_off_is_kept_forever():
    _, prune = _prune([_rec(status="in_transit", monitor=False, days_ago=999)])
    assert prune == []


def test_missing_change_at_is_backfilled_not_pruned():
    rec = _rec(num="M", status="delivered", change_at=None)
    backfill, prune = _prune([rec])
    assert backfill == [rec] and prune == []


def test_unparseable_change_at_is_backfilled_not_pruned():
    rec = _rec(num="U", status="delivered", change_at="not-a-date")
    backfill, prune = _prune([rec])
    assert backfill == [rec] and prune == []


def test_delivered_days_zero_prunes_all_delivered_regardless_of_age():
    rec = _rec(num="D", status="delivered", days_ago=0)
    _, prune = _prune([rec], delivered_days=0)
    assert prune == [rec]


def test_mixed_batch_partitions_correctly():
    keep = _rec(num="keep", status="in_transit", days_ago=1)
    delivered = _rec(num="del", status="delivered", days_ago=5)
    stale = _rec(num="stale", status="in_transit", days_ago=40)
    missing = _rec(num="miss", status="delivered", change_at=None)
    backfill, prune = _prune([keep, delivered, stale, missing])
    assert backfill == [missing]
    assert set(r["tracking_number"] for r in prune) == {"del", "stale"}
