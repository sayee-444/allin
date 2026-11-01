"""DORA metrics and code health for AllIn: ``venv/bin/python -m scripts.metrics``.

DORA comes from GitHub, where a merge into ``main`` counts as a deploy; code health from the
checks the issue workflow runs.
"""

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import median
from typing import Any

from pydantic import BaseModel, TypeAdapter

_WINDOW = timedelta(days=28)


@dataclass(frozen=True)
class Change:
    """A pull request merged into ``main``."""

    first_commit_at: datetime
    merged_at: datetime
    fix: bool  # it repairs something an earlier change broke


@dataclass(frozen=True)
class Incident:
    """An issue labelled ``bug``: something broken from when it was opened until closed."""

    opened_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class Dora:
    deploys_per_week: float
    lead_time: timedelta | None  # median, first commit to merge; None without changes
    change_failure_rate: float | None  # share of merges that are fixes; None without changes
    time_to_restore: timedelta | None  # median time a recent incident stayed open


def compute_dora(changes: list[Change], incidents: list[Incident], now: datetime) -> Dora:
    """The four DORA metrics over the last four weeks."""
    recent = [change for change in changes if now - change.merged_at <= _WINDOW]
    lead_times = [change.merged_at - change.first_commit_at for change in recent]
    restores = [
        incident.closed_at - incident.opened_at
        for incident in incidents
        if incident.closed_at is not None and now - incident.closed_at <= _WINDOW
    ]
    return Dora(
        deploys_per_week=len(recent) / (_WINDOW.days / 7),
        lead_time=_median_duration(lead_times),
        change_failure_rate=sum(change.fix for change in recent) / len(recent) if recent else None,
        time_to_restore=_median_duration(restores),
    )


def _median_duration(durations: list[timedelta]) -> timedelta | None:
    if not durations:
        return None
    return timedelta(seconds=median(duration.total_seconds() for duration in durations))


# Reading GitHub and the repository; ``main`` prints the report.


class _Commit(BaseModel):
    committedDate: datetime  # noqa: N815 -- gh's JSON field name


class _PullRequest(BaseModel):
    title: str
    headRefName: str  # noqa: N815 -- gh's JSON field name
    mergedAt: datetime  # noqa: N815 -- gh's JSON field name
    additions: int
    deletions: int
    commits: list[_Commit]


class _Issue(BaseModel):
    createdAt: datetime  # noqa: N815 -- gh's JSON field name
    closedAt: datetime | None  # noqa: N815 -- gh's JSON field name


_ESCAPES = re.compile(r"\bcast\(|pyright: ignore|type: ignore|noqa")


def _gh(*args: str) -> str:
    # gh comes from PATH, as in the user's own shell.
    command = ["gh", *args]
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout


def _merged_pull_requests() -> list[_PullRequest]:
    # GitHub refuses more than ~30 pull requests with their commits in one query; at more than
    # 7 merges a week the four-week window would need paging.
    fields = "title,headRefName,mergedAt,additions,deletions,commits"
    raw = _gh(
        "pr", "list", "--state", "merged", "--base", "main", "--limit", "30", "--json", fields
    )
    return TypeAdapter(list[_PullRequest]).validate_json(raw)


def _is_fix(pull: _PullRequest) -> bool:
    return pull.headRefName.startswith("fix/") or pull.title.startswith("fix")


def _changes(pulls: list[_PullRequest]) -> list[Change]:
    return [
        Change(
            first_commit_at=min(commit.committedDate for commit in pull.commits),
            merged_at=pull.mergedAt,
            fix=_is_fix(pull),
        )
        for pull in pulls
        if pull.commits
    ]


def _incidents() -> list[Incident]:
    raw = _gh("issue", "list", "--label", "bug", "--state", "all", "--json", "createdAt,closedAt")
    issues = TypeAdapter(list[_Issue]).validate_json(raw)
    return [Incident(opened_at=issue.createdAt, closed_at=issue.closedAt) for issue in issues]


def _run(*command: str) -> str:
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    return result.stdout


def _coverage() -> str:
    out = _run(
        "venv/bin/pytest", "-q", "-p", "no:cacheprovider", "--cov=allin", "--cov-report=term"
    )
    total = next((line for line in out.splitlines() if line.startswith("TOTAL")), "")
    passed = next((line for line in out.splitlines() if " passed" in line), "?")
    return f"{passed.strip()}; coverage {total.split()[-1] if total else '?'}"


def _type_errors() -> int:
    report: dict[str, Any] = json.loads(_run("venv/bin/basedpyright", "--outputjson"))
    errors: int = report["summary"]["errorCount"]
    return errors


def _lint_warnings() -> int:
    findings: list[Any] = json.loads(
        _run("venv/bin/ruff", "check", "--output-format", "json", "src", "tests")
    )
    return len(findings)


def _escapes() -> int:
    return sum(
        len(_ESCAPES.findall(path.read_text(encoding="utf-8")))
        for path in Path("src").rglob("*.py")
    )


def _hours(delta: timedelta | None) -> str:
    return "no data" if delta is None else f"{delta.total_seconds() / 3600:.1f} h"


def _percent(rate: float | None) -> str:
    return "no data" if rate is None else f"{rate:.0%}"


def main() -> None:
    pulls = _merged_pull_requests()
    dora = compute_dora(_changes(pulls), _incidents(), datetime.now(UTC))
    sizes = sorted(pull.additions + pull.deletions for pull in pulls)
    lines = [
        "DORA, last 4 weeks (a merge into main is a deploy)",
        f"  deploys per week      {dora.deploys_per_week:.1f}",
        f"  lead time (median)    {_hours(dora.lead_time)}",
        f"  change failure rate   {_percent(dora.change_failure_rate)}  (fix/ branches or fix: titles)",
        f"  time to restore       {_hours(dora.time_to_restore)}  (issues labelled bug)",
        "Code health",
        f"  tests                 {_coverage()}",
        f"  type errors           {_type_errors()}  (basedpyright strict)",
        f"  lint warnings         {_lint_warnings()}  (ruff)",
        f"  escapes in src        {_escapes()}  (cast, pyright/type: ignore, noqa)",
        f"  PR size (median)      {median(sizes) if sizes else 'no data'} lines changed",
    ]
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
