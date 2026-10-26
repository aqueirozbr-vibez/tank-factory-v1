// The eval sets and where each stands (PRD Capability 5: ≥ 20 closed issues in the fixed class
// mix; pass rate overall and per class; a second installation within 10 points of the pilot).
// Server component: per set, the latest complete run and the gap to the reference pass rate.

import { CLASSES, GAP_POINTS, classRate, gapOf, gapText, latestComplete, points, type EvalRun, type EvalSet } from "@/lib/eval";

import { Chip } from "./Chip";
import styles from "./EvalSetSummary.module.css";

export type EvalSetSummaryProps = { sets: EvalSet[]; runs: EvalRun[] };

export function EvalSetSummary({ sets, runs }: EvalSetSummaryProps) {
  return (
    <section aria-labelledby="sets-title" className={styles.section}>
      <h2 id="sets-title" className={styles.heading}>
        Eval sets <span className="mono text-muted">{sets.length}</span>
      </h2>
      {sets.length === 0 ? (
        <p className="text-muted">No eval set yet: `factory eval add` builds one from closed issues in the fixed class mix.</p>
      ) : (
        <div className={styles.grid}>
          {sets.map((set) => {
            const latest = latestComplete(runs, set.id);
            const gap = gapOf(set, latest);
            return (
              <article key={set.id} className={`card blueprint ${styles.card}`} aria-label={`Eval set ${set.name}`}>
                <i className="corner tl" />
                <i className="corner tr" />
                <i className="corner bl" />
                <i className="corner br" />
                <p className="card-kicker mono">{set.repo}</p>
                <h3 className={styles.name}>{set.name}</h3>
                <p className={`${styles.meta} mono text-muted`}>
                  {set.items} items · set {set.id}
                </p>
                {latest ? (
                  <>
                    <p className={styles.rate}>
                      <span className={`${styles.big} mono`}>{points(latest.pass_rate)}</span>
                      <span className="text-muted">% pass rate · latest complete run</span>
                    </p>
                    <ul className={`${styles.classes} mono`}>
                      {CLASSES.map((cls) => (
                        <li key={cls.key}>
                          <span className="text-muted">{cls.label}</span> {points(classRate(latest, cls.key))}
                        </li>
                      ))}
                    </ul>
                  </>
                ) : (
                  <p className="text-muted">No complete run yet.</p>
                )}
                <p className={styles.gap}>
                  {gap ? <Chip kind="gate" value={gap.within ? "pass" : "fail"} title={`within ${GAP_POINTS} points of the pilot`} /> : null}
                  <span className={gap ? "" : "text-muted"}>
                    {set.reference_pass_rate !== null && set.reference_pass_rate !== undefined ? `reference ${points(set.reference_pass_rate)} · ${gapText(gap)}` : "no reference pass rate (the pilot itself)"}
                  </span>
                </p>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
