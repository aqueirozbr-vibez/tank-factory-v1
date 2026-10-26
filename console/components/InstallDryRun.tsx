// What the install dry run set (PRD Capability 5 · Cost control: the per-task budget at 2× the
// highest cost over a 3-issue dry run, never below the industry per-task cap of US$5 (spec 67),
// then 2× the trailing 30-day median from day 8; the daily cap N × 3 × the per-task budget).
// Server component over the installation read, which carries the last derivation.

import type { Installation } from "@/lib/eval";

import styles from "./InstallDryRun.module.css";

export type InstallDryRunProps = { installation: Installation | null };

type DryRunIssue = { issue?: number; completed?: boolean; cost_usd?: number; blocked_reason?: string | null };

export function budgetModeText(mode: Installation["budget_mode"]): string {
  return mode === "install"
    ? "install value: 2× the highest cost of the 3-issue dry run, never below the industry cap"
    : "median: 2× the trailing 30-day median cost per task";
}

export function derivationIssues(note: NonNullable<Installation["last_dry_run"]>): DryRunIssue[] {
  return note.issues.filter((i): i is DryRunIssue => typeof i === "object" && i !== null);
}

/** "2 × 0.34 = 0.68 → floor 5.00" or "2 × 3.10 = 6.20". */
export function derivationText(note: NonNullable<Installation["last_dry_run"]>): string {
  if (note.highest_cost_usd === null || note.highest_cost_usd === undefined) return note.reason;
  const raw = Math.round(note.factor * note.highest_cost_usd * 100) / 100;
  const base = `${note.factor} × ${note.highest_cost_usd.toFixed(2)} = ${raw.toFixed(2)}`;
  if (note.floor_usd !== null && note.floor_usd !== undefined && note.per_task_budget_usd !== null && note.per_task_budget_usd !== undefined && raw < note.per_task_budget_usd) {
    return `${base} → floor ${note.per_task_budget_usd.toFixed(2)}`;
  }
  return base;
}

export function InstallDryRun({ installation }: InstallDryRunProps) {
  const note = installation?.last_dry_run ?? null;
  return (
    <section aria-labelledby="dryrun-title" className={`card blueprint ${styles.card}`}>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
      <h2 id="dryrun-title" className={styles.heading}>
        Install dry run and budget
      </h2>
      {installation ? (
        <dl className={styles.facts}>
          <dt>Factory version</dt>
          <dd className="mono">{installation.factory_version}</dd>
          <dt>Configuration</dt>
          <dd className="mono">{installation.config_sha.slice(0, 12)}</dd>
          <dt>Budget mode</dt>
          <dd>
            <span className="mono">{installation.budget_mode}</span> · {budgetModeText(installation.budget_mode)}
          </dd>
          <dt>Per-task budget</dt>
          <dd className="mono">${installation.per_task_budget_usd.toFixed(2)}</dd>
          <dt>Daily cap</dt>
          <dd className="mono">
            ${installation.daily_cap_usd.toFixed(2)} = N {installation.concurrency_n} × 3 × ${installation.per_task_budget_usd.toFixed(2)}
          </dd>
          <dt>Derivation</dt>
          <dd>
            {note ? (
              <span className="mono" data-testid="derivation">
                {derivationText(note)}
              </span>
            ) : (
              <span className="text-muted">no dry run recorded in this trace</span>
            )}
          </dd>
        </dl>
      ) : (
        <p className="text-muted">The installation is not readable.</p>
      )}
      {note ? (
        <table className={`table ${styles.table}`} aria-label="Dry-run issues">
          <caption className={`${styles.caption} mono text-muted`}>
            {note.outcome} · {note.completed} of {note.required} needed completed · {note.at}
          </caption>
          <thead>
            <tr>
              <th scope="col">Issue</th>
              <th scope="col">Completed</th>
              <th scope="col" className={styles.num}>
                Cost
              </th>
              <th scope="col">Blocked</th>
            </tr>
          </thead>
          <tbody>
            {derivationIssues(note).map((issue, index) => (
              <tr key={index}>
                <td className="mono">#{issue.issue ?? "?"}</td>
                <td className="mono">{issue.completed ? "yes" : "no"}</td>
                <td className={`${styles.num} mono`}>{issue.cost_usd !== undefined ? `$${issue.cost_usd.toFixed(2)}` : "—"}</td>
                <td className="mono">{issue.blocked_reason ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {note ? <p className={`${styles.reason} text-muted`}>{note.reason}</p> : null}
    </section>
  );
}
