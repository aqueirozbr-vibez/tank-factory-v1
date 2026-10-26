// The changes a pass-rate drop over 10 points blocked, with the regression that blocked them
// (PRD Capability 5; ui-context.md · Layout Patterns · Eval and versions). Server component.

import { BLOCK_DROP_POINTS, blockedChanges, points, resultLine, signed, triggerText, versionOf, type EvalRun } from "@/lib/eval";

import styles from "./BlockedChange.module.css";

export type BlockedChangeProps = { runs: EvalRun[] };

export function BlockedChange({ runs }: BlockedChangeProps) {
  const blocked = blockedChanges(runs);
  return (
    <section aria-labelledby="blocked-title" className={styles.section}>
      <h2 id="blocked-title" className={styles.heading}>
        Blocked changes <span className="mono text-muted">{blocked.length}</span>
      </h2>
      <p className={`${styles.rule} text-muted`}>A drop of more than {BLOCK_DROP_POINTS} points against the merged baseline blocks the change.</p>
      {blocked.length === 0 ? (
        <p className="text-muted">No change has been blocked by the eval set.</p>
      ) : (
        <ul className={styles.list}>
          {blocked.map(({ run, baseline, regressions }) => (
            <li key={run.id} className={styles.item} data-run={run.id}>
              <div className={styles.head}>
                <span className={`${styles.blocked} mono`}>blocked</span>
                <span className="mono">{versionOf(run)}</span>
                <span>{triggerText(run.trigger)}</span>
                {run.trigger_ref ? <span className="mono text-muted">{run.trigger_ref}</span> : null}
                <span className={`${styles.delta} mono`}>{signed(run.delta_points)} pt</span>
              </div>
              <p className={styles.line}>{resultLine(run, baseline)}</p>
              {regressions.length > 0 ? (
                <ul className={`${styles.regressions} mono`}>
                  {regressions.map((r) => (
                    <li key={r.key}>
                      {r.label} {points(r.from)} → {points(r.to)} ({signed(-r.drop)})
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-muted">The baseline run is not in the trace; per-class regression unknown.</p>
              )}
              <p className={`${styles.meta} mono text-muted`}>
                run {run.id} · baseline {run.baseline_run_id ?? "none"} · {run.started_at}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
