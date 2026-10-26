"""KPIs materialised into ``kpi_daily`` and the monthly audit sampler (PRD §Metrics; TRD §4.2, §9).

The five headline numbers and the leading indicators are computed from the
trace by code and written as one row per metric and local day (value,
numerator, denominator), nightly and on every merge, so the console reads a
table and never computes per request. The audit queue is drawn by code:
every ``risk:high`` and ``risk:governed`` merge, and a Bernoulli 0.10 draw
of the ``risk:low`` ones from a hash of the task id and the month, so the
sample is reproducible and never chosen by a person. No metric is keyed by
a login.
"""

import asyncio
import hashlib
import json
import sqlite3
import statistics
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from orchestrator.clock import parse_ts
from orchestrator.models.enums import EventKind
from orchestrator.trace.writer import TraceWriter

WINDOW_DAYS = 30
STUCK_WINDOW_DAYS = 7
AUDIT_LOW_RATE = 0.10
STRATA = ("risk:low", "risk:high", "risk:governed")
HEADLINE = (
    "factory_merge_share",
    "merge_rate",
    "unattended_rate",
    "audit_disagreement_low",
    "cost_per_merged_pr",
)
MERGED_LOG = '%"merged":%'  # the merge observer's LOG payload
Clock = Callable[[], datetime]


def utc_clock() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class Metric:
    name: str
    value: float | None
    numerator: float | None = None
    denominator: float | None = None


def ratio(numerator: float, denominator: float, name: str) -> Metric:
    """A percentage, or ``None`` when nothing happened yet (no fake zero)."""
    value = round(100.0 * numerator / denominator, 2) if denominator else None
    return Metric(name, value, numerator, denominator)


def audit_sampled(task_id: str, month: str, stratum: str) -> bool:
    """Every high and governed merge; ``risk:low`` by a reproducible coin at 0.10."""
    if stratum in ("risk:high", "risk:governed"):
        return True
    digest = hashlib.sha256(f"{task_id}|{month}".encode()).digest()
    coin = int.from_bytes(digest[:8], "big") / 2**64
    return coin < AUDIT_LOW_RATE


def local_day(now: datetime, timezone: str) -> str:
    return now.astimezone(ZoneInfo(timezone)).date().isoformat()


def local_month(now: datetime, timezone: str) -> str:
    return now.astimezone(ZoneInfo(timezone)).strftime("%Y-%m")


class KpiMaterialiser:
    def __init__(self, writer: TraceWriter, *, clock: Clock = utc_clock) -> None:
        self.writer = writer
        self.clock = clock
        self.last_day: str | None = None

    # --- the audit sampler --------------------------------------------------------------------

    def sample_task(self, task_id: str, *, month: str) -> bool | None:
        """Draw one merged task for the month; ``None`` when it is not merged or already drawn."""
        task = self.writer.get_task(task_id)
        if task is None or task["status"] != "merged" or task["pr_number"] is None:
            return None
        stratum = str(task["risk"] or "risk:low")
        if stratum not in STRATA:
            stratum = "risk:low"
        sampled = audit_sampled(task_id, month, stratum)
        inserted = self.writer.insert_audit_decision(
            task_id=task_id,
            pr_number=int(task["pr_number"]),
            month=month,
            stratum=stratum,
            sampled=sampled,
        )
        return sampled if inserted else None

    def sample_month(self, month: str) -> list[str]:
        """Every task merged in ``month`` without a draw gets one; returns the ids drawn in."""
        drawn: list[str] = []
        for row in self.writer.merged_in_month(month):
            if self.sample_task(str(row["task_id"]), month=month):
                drawn.append(str(row["task_id"]))
        return drawn

    # --- the metrics --------------------------------------------------------------------------

    def compute(self, *, now: datetime | None = None) -> list[Metric]:
        moment = now or self.clock()
        since = _iso(moment - timedelta(days=WINDOW_DAYS))
        week = _iso(moment - timedelta(days=STUCK_WINDOW_DAYS))
        month = local_month(moment, self._timezone())
        admitted = self.writer.db.fetchall(
            "SELECT task_id, status, budget_usd, spent_usd FROM tasks WHERE created_at >= ?",
            (since,),
        )
        metrics = self._merge_metrics(since)
        metrics += self._task_metrics(admitted)
        metrics += self._audit_metrics(month)
        metrics += self._cost_metrics(admitted, since)
        metrics += self._indicator_metrics(since, week)
        metrics += self._eval_metrics()
        return metrics

    def _merge_metrics(self, since: str) -> list[Metric]:
        factory = self._count(
            "SELECT COUNT(*) FROM events WHERE kind = 'log' AND task_id IS NOT NULL"
            " AND payload_json LIKE ? AND created_at >= ?",
            (MERGED_LOG, since),
        )
        other = self._count(
            "SELECT COUNT(*) FROM events WHERE kind = 'log' AND payload_json LIKE ?"
            " AND payload_json LIKE ? AND created_at >= ?",
            ('%"merge":"ignored"%', '%"kind":"pull_request_merged"%', since),
        )
        opened = self._count(
            "SELECT COUNT(*) FROM events WHERE kind = 'handoff' AND payload_json LIKE ?"
            " AND created_at >= ?",
            ('%"created":true%', since),
        )
        return [
            ratio(factory, factory + other, "factory_merge_share"),
            ratio(factory, opened, "merge_rate"),
        ]

    def _task_metrics(self, admitted: Sequence[sqlite3.Row]) -> list[Metric]:
        reached = [r for r in admitted if r["status"] in ("pr_open", "merged", "blocked")]
        unattended = sum(1 for r in reached if not self._attended(str(r["task_id"])))
        blocked = sum(1 for r in admitted if r["status"] == "blocked")
        return [
            ratio(unattended, len(admitted), "unattended_rate"),
            ratio(blocked, len(admitted), "escalation_rate"),
        ]

    def _attended(self, task_id: str) -> bool:
        """A human phase (``engineer`` kind) before the PR: plan approval; ``ci_wait`` is after."""
        found = self.writer.db.scalar(
            "SELECT 1 FROM phases p JOIN runs r ON r.run_id = p.run_id WHERE r.task_id = ?"
            " AND p.kind = 'engineer' AND p.name != 'ci_wait' LIMIT 1",
            (task_id,),
        )
        return found is not None

    def _audit_metrics(self, month: str) -> list[Metric]:
        out: list[Metric] = []
        for stratum in STRATA:
            rows = self.writer.db.fetchall(
                "SELECT decision FROM audit_decisions WHERE month = ? AND stratum = ?"
                " AND sampled = 1",
                (month, stratum),
            )
            disagree = sum(1 for r in rows if r["decision"] == "disagree")
            out.append(ratio(disagree, len(rows), f"audit_disagreement_{stratum.split(':')[1]}"))
        return out

    def _cost_metrics(self, admitted: Sequence[sqlite3.Row], since: str) -> list[Metric]:
        db = self.writer.db
        costs = [
            float(r["cost"] or 0.0)
            for r in db.fetchall(
                "SELECT (SELECT COALESCE(SUM(total_cost_usd), 0) FROM runs"
                " WHERE runs.task_id = t.task_id) AS cost FROM tasks t"
                " WHERE t.status = 'merged' AND EXISTS (SELECT 1 FROM events e"
                " WHERE e.task_id = t.task_id AND e.kind = 'log' AND e.payload_json LIKE ?"
                " AND e.created_at >= ?)",
                (MERGED_LOG, since),
            )
        ]
        total = round(sum(costs), 6)
        over = {
            str(r["task_id"])
            for r in db.fetchall(
                "SELECT DISTINCT task_id FROM events WHERE kind = 'budget_stop'"
                " AND task_id IS NOT NULL AND created_at >= ?",
                (since,),
            )
        }
        over |= {
            str(t["task_id"]) for t in admitted if float(t["spent_usd"]) > float(t["budget_usd"])
        }
        return [
            Metric(
                "cost_per_merged_pr",
                round(total / len(costs), 4) if costs else None,
                total,
                float(len(costs)),
            ),
            Metric(
                "cost_per_merged_pr_median_30d",
                round(statistics.median(costs), 4) if costs else None,
                None,
                float(len(costs)),
            ),
            Metric("over_budget_count", float(len(over)), float(len(over)), None),
        ]

    def _indicator_metrics(self, since: str, week: str) -> list[Metric]:
        db = self.writer.db
        built = db.fetchall(
            "SELECT (SELECT COUNT(*) FROM phases f JOIN runs fr ON fr.run_id = f.run_id"
            " WHERE fr.task_id = t.task_id AND f.name = 'code_gates' AND f.status = 'fail')"
            " AS repairs FROM tasks t WHERE t.created_at >= ? AND EXISTS (SELECT 1 FROM phases b"
            " JOIN runs br ON br.run_id = b.run_id WHERE br.task_id = t.task_id"
            " AND b.name = 'build')",
            (since,),
        )
        rounds = sum(int(r["repairs"]) for r in built)
        minutes: list[float] = []
        for row in db.fetchall(
            "SELECT e.created_at AS merged_at, (SELECT MIN(h.created_at) FROM events h"
            " WHERE h.task_id = e.task_id AND h.kind = 'handoff' AND h.payload_json LIKE ?)"
            " AS opened_at FROM events e WHERE e.kind = 'log' AND e.task_id IS NOT NULL"
            " AND e.payload_json LIKE ? AND e.created_at >= ?",
            ('%"pr":%', MERGED_LOG, since),
        ):
            if row["opened_at"] is not None:
                delta = parse_ts(str(row["merged_at"])) - parse_ts(str(row["opened_at"]))
                minutes.append(max(0.0, delta.total_seconds() / 60.0))
        attempts = self._count(
            "SELECT COUNT(*) FROM events WHERE payload_json LIKE ? AND created_at >= ?",
            ('%"protected_path_attempt"%', since),
        )
        stuck = self._count(
            "SELECT COUNT(*) FROM events WHERE kind = 'log' AND payload_json LIKE ?"
            " AND created_at >= ?",
            ('%"stuck":true%', week),
        )
        overrides = self._count(
            "SELECT COUNT(*) FROM events WHERE kind = 'audit' AND payload_json LIKE ?"
            " AND created_at >= ?",
            ('%"override":%', since),
        )
        reviews = self._count(
            "SELECT COUNT(*) FROM phases WHERE name = 'review' AND started_at >= ?", (since,)
        )
        return [
            Metric(
                "repair_rounds_mean",
                round(rounds / len(built), 4) if built else None,
                float(rounds),
                float(len(built)),
            ),
            Metric(
                "review_minutes_median",
                round(statistics.median(minutes), 2) if minutes else None,
                None,
                float(len(minutes)),
            ),
            Metric("protected_path_attempts", float(attempts), float(attempts), None),
            Metric("stuck_runs_7d", float(stuck), float(stuck), None),
            ratio(overrides, reviews, "verdict_override_rate"),
        ]

    def _eval_metrics(self) -> list[Metric]:
        db = self.writer.db
        latest = db.fetchone(
            "SELECT pass_rate, per_class_json, set_id FROM eval_runs WHERE status = 'complete'"
            " AND pass_rate IS NOT NULL ORDER BY started_at DESC, id DESC LIMIT 1"
        )
        if latest is None:
            return []
        rate = float(latest["pass_rate"])
        out = [Metric("eval_pass_rate", rate)]
        per_class = json.loads(latest["per_class_json"] or "{}")
        for eval_class, value in sorted(per_class.items()):
            out.append(Metric(f"eval_pass_rate_{eval_class}", float(value)))
        reference = db.scalar(
            "SELECT reference_pass_rate FROM eval_sets WHERE id = ?", (latest["set_id"],)
        )
        if reference is not None:
            gap = round(rate - float(reference), 2)
            out.append(Metric("eval_gap_points", gap, rate, float(reference)))
        return out

    def _count(self, sql: str, params: Sequence[object] = ()) -> int:
        return int(self.writer.db.scalar(sql, params) or 0)

    def _timezone(self) -> str:
        installation = self.writer.get_installation()
        return str(installation["timezone"]) if installation is not None else "UTC"

    # --- writing and triggers -----------------------------------------------------------------

    def materialise(self, *, now: datetime | None = None) -> list[Metric]:
        """Compute every metric and upsert one ``kpi_daily`` row per metric for the local day."""
        moment = now or self.clock()
        day = local_day(moment, self._timezone())
        metrics = self.compute(now=moment)
        for metric in metrics:
            self.writer.upsert_kpi(
                day,
                metric.name,
                value=metric.value,
                numerator=metric.numerator,
                denominator=metric.denominator,
            )
        self.last_day = day
        self.writer.write_event(
            EventKind.LOG,
            "orchestrator",
            payload={"kpi": "materialised", "day": day, "metrics": len(metrics)},
        )
        return metrics

    def on_merge(self, task_id: str) -> None:
        """A merge draws the task for the month and refreshes the day's numbers (TRD §9)."""
        now = self.clock()
        self.sample_task(task_id, month=local_month(now, self._timezone()))
        self.materialise(now=now)

    async def run(self, stop: asyncio.Event, *, interval_s: float = 60.0) -> None:
        """Materialise at start and on every local day change (the nightly refresh)."""
        while not stop.is_set():
            now = self.clock()
            timezone = self._timezone()
            if local_day(now, timezone) != self.last_day:
                self.sample_month(local_month(now, timezone))
                self.materialise(now=now)
            try:
                await asyncio.wait_for(stop.wait(), interval_s)
            except TimeoutError:
                continue


__all__ = [
    "AUDIT_LOW_RATE",
    "HEADLINE",
    "STRATA",
    "WINDOW_DAYS",
    "KpiMaterialiser",
    "Metric",
    "audit_sampled",
    "local_day",
    "local_month",
    "ratio",
]
