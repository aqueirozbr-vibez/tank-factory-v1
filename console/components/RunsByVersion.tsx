// Runs by factory and configuration version (ui-context.md · Layout Patterns · Eval and
// versions; PRD: pass rate overall and per class by factory, prompt and roster version). Server
// component: one comparison table per version, the current configuration marked.

import { runsByVersion, type EvalRun, type HistoryItem } from "@/lib/eval";

import { EvalComparisonTable } from "./EvalComparisonTable";
import styles from "./RunsByVersion.module.css";

export type RunsByVersionProps = { runs: EvalRun[]; history: HistoryItem[] };

export function RunsByVersion({ runs, history }: RunsByVersionProps) {
  const groups = runsByVersion(runs, history);
  return (
    <section aria-labelledby="versions-title" className={styles.section}>
      <h2 id="versions-title" className={styles.heading}>
        Runs by version <span className="mono text-muted">{groups.length}</span>
      </h2>
      {groups.length === 0 ? (
        <p className="text-muted">No eval run yet. The set runs on every change to prompts, roster, gates, instruments or harness version.</p>
      ) : (
        groups.map((group) => (
          <div key={group.key} className={styles.group} data-current={group.current ? "true" : "false"}>
            <h3 className={`${styles.version} mono`}>
              {group.key}
              {group.current ? <span className={styles.current}>current configuration</span> : null}
              <span className="text-muted">
                {group.runs.length} run{group.runs.length === 1 ? "" : "s"}
              </span>
            </h3>
            <EvalComparisonTable runs={group.runs} />
          </div>
        ))
      )}
    </section>
  );
}
