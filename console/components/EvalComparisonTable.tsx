// Columns Version, Trigger, Overall, bugs, tests, docs, small features, Δ overall, Result
// (ui-context.md · Component Library · EvalComparisonTable): whole points against the merged
// baseline; Δ coloured by role with its sign; a drop over 10 points reads "blocked".

import { CLASSES, classRate, points, resultOf, signed, triggerText, versionOf, type EvalRun } from "@/lib/eval";

import { Chip } from "./Chip";
import styles from "./EvalComparisonTable.module.css";

export type EvalComparisonTableProps = { runs: EvalRun[]; caption?: string };

export function deltaRole(run: Pick<EvalRun, "delta_points" | "blocked">): "pass" | "fail" | "neutral" {
  if (run.blocked === true) return "fail";
  if (run.delta_points === null || run.delta_points === undefined) return "neutral";
  return run.delta_points < 0 ? "fail" : run.delta_points > 0 ? "pass" : "neutral";
}

export function resultChip(run: Pick<EvalRun, "status" | "blocked">): { kind: "gate"; value: string } {
  const result = resultOf(run);
  return { kind: "gate", value: result === "blocked" ? "fail" : result === "passed" ? "pass" : result === "running" ? "running" : result === "failed" ? "fail" : "pending" };
}

export function EvalComparisonTable({ runs, caption }: EvalComparisonTableProps) {
  return (
    <table className={`table ${styles.table}`}>
      {caption ? <caption className={`${styles.caption} mono text-muted`}>{caption}</caption> : null}
      <thead>
        <tr>
          <th scope="col">Version</th>
          <th scope="col">Trigger</th>
          <th scope="col" className={styles.num}>
            Overall
          </th>
          {CLASSES.map((cls) => (
            <th key={cls.key} scope="col" className={styles.num}>
              {cls.label}
            </th>
          ))}
          <th scope="col" className={styles.num}>
            Δ overall
          </th>
          <th scope="col">Result</th>
        </tr>
      </thead>
      <tbody>
        {runs.map((run) => {
          const result = resultOf(run);
          const chip = resultChip(run);
          return (
            <tr key={run.id} data-result={result}>
              <td className="mono" title={run.config_sha}>
                {versionOf(run)}
              </td>
              <td>
                {triggerText(run.trigger)}
                {run.trigger_ref ? <span className={`${styles.ref} mono text-muted`}> {run.trigger_ref}</span> : null}
              </td>
              <td className={`${styles.num} mono`}>{points(run.pass_rate)}</td>
              {CLASSES.map((cls) => (
                <td key={cls.key} className={`${styles.num} mono`}>
                  {points(classRate(run, cls.key))}
                </td>
              ))}
              <td className={`${styles.num} ${styles.delta} mono`} data-role={deltaRole(run)}>
                {signed(run.delta_points)}
              </td>
              <td>
                <div className={styles.result}>
                  <Chip kind={chip.kind} value={chip.value} />
                  <span className="mono" data-testid="result-text">
                    {result}
                  </span>
                </div>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
