from datetime import UTC, datetime, timedelta

from scripts.metrics import Change, Incident, compute_dora

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)


def change(days_ago: float, lead_hours: float = 1, *, fix: bool = False) -> Change:
    merged = NOW - timedelta(days=days_ago)
    return Change(first_commit_at=merged - timedelta(hours=lead_hours), merged_at=merged, fix=fix)


def test_deployment_frequency_counts_merges_per_week_in_the_last_four_weeks():
    changes = [change(1), change(3), change(10), change(20), change(40)]
    assert compute_dora(changes, [], NOW).deploys_per_week == 1.0


def test_lead_time_is_the_median_from_first_commit_to_merge():
    changes = [change(1, lead_hours=1), change(2, lead_hours=3), change(3, lead_hours=20)]
    assert compute_dora(changes, [], NOW).lead_time == timedelta(hours=3)


def test_change_failure_rate_is_the_share_of_recent_merges_that_fix_an_earlier_one():
    changes = [change(1, fix=True), change(2), change(3), change(4), change(40, fix=True)]
    assert compute_dora(changes, [], NOW).change_failure_rate == 0.25


def test_time_to_restore_is_the_median_time_recent_incidents_stayed_open():
    def incident(days_ago: float, open_hours: float) -> Incident:
        closed = NOW - timedelta(days=days_ago)
        return Incident(opened_at=closed - timedelta(hours=open_hours), closed_at=closed)

    still_open = Incident(opened_at=NOW - timedelta(days=1), closed_at=None)
    incidents = [incident(1, 2), incident(5, 6), incident(40, 100), still_open]
    assert compute_dora([], incidents, NOW).time_to_restore == timedelta(hours=4)


def test_without_changes_or_incidents_there_is_nothing_to_measure():
    dora = compute_dora([], [], NOW)
    assert (dora.lead_time, dora.change_failure_rate, dora.time_to_restore) == (None, None, None)
