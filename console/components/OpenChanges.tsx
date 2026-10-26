// Open `.factory/` change PRs as cards (ui-context.md · Component Library · ConfigChangeCard):
// file, PR link, unified diff lines, the eval result line "Overall 63 → 50 (−13 pt). bugs 67 →
// 44.", footer "Opened by @login · reason · not applied until merged"; "Eval running" while the
// set replays; the error role when the eval blocked the change. Server components.

import { resultLine, resultOf, runById, type EvalRun } from "@/lib/eval";
import type { OpenChange } from "@/lib/config";

import styles from "./OpenChanges.module.css";

export type OpenChangesProps = { changes: OpenChange[]; runs: EvalRun[]; note: string | null };

export function evalLine(change: OpenChange, runs: EvalRun[]): { text: string; role: "pass" | "fail" | "info" | "neutral" } {
  const run = change.eval;
  if (!run) return { text: "No eval run recorded for this PR yet.", role: "neutral" };
  const result = resultOf(run);
  if (result === "running") return { text: "Eval running", role: "info" };
  if (result === "failed") return { text: "Eval failed to complete; the change stays unapplied.", role: "fail" };
  const baseline = run.baseline_run_id ? (runById(runs).get(run.baseline_run_id) ?? null) : null;
  const line = resultLine(run, baseline);
  return result === "blocked" ? { text: `Eval blocked the change. ${line}`, role: "fail" } : { text: `${line} Eval passed.`, role: "pass" };
}

export function diffLines(patch: string | null): Array<{ kind: "add" | "del" | "hunk" | "ctx"; text: string }> {
  if (!patch) return [];
  return patch.split("\n").map((text) => ({
    kind: text.startsWith("+") ? "add" : text.startsWith("-") ? "del" : text.startsWith("@@") ? "hunk" : "ctx",
    text,
  }));
}

export function ConfigChangeCard({ change, runs }: { change: OpenChange; runs: EvalRun[] }) {
  const line = evalLine(change, runs);
  return (
    <article className={`card blueprint ${styles.card}`} aria-label={`Change PR #${change.pull.number}`} data-eval={line.role}>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
      <header className={styles.head}>
        <span className="mono">{change.files.map((f) => f.filename).join(", ") || "(no file)"}</span>
        <a href={change.pull.html_url} target="_blank" rel="noreferrer" className={`${styles.link} mono`}>
          PR #{change.pull.number} ↗
        </a>
      </header>
      {change.files.map((file) => (
        <pre key={file.filename} className={`${styles.diff} mono`} aria-label={`Diff of ${file.filename}`} tabIndex={0}>
          {diffLines(file.patch).map((l, index) => (
            <span key={index} className={styles[l.kind]} data-kind={l.kind}>
              {l.text}
              {"\n"}
            </span>
          ))}
        </pre>
      ))}
      <p className={styles.eval} data-role={line.role} data-testid="eval-line">
        {line.text}
      </p>
      <footer className={`${styles.footer} text-muted`}>
        Opened by <span className="mono">@{change.pull.user}</span> · {change.reason ?? "no reason recorded"} · not applied until merged
      </footer>
    </article>
  );
}

export function OpenChanges({ changes, runs, note }: OpenChangesProps) {
  return (
    <section aria-labelledby="changes-title" className={styles.section}>
      <h2 id="changes-title" className={styles.heading}>
        Open changes <span className="mono text-muted">{changes.length}</span>
      </h2>
      {note ? <p className="text-muted">{note}</p> : null}
      {changes.length === 0 && !note ? <p className="text-muted">No `.factory/` change is open.</p> : null}
      <div className={styles.cards}>
        {changes.map((change) => (
          <ConfigChangeCard key={change.pull.number} change={change} runs={runs} />
        ))}
      </div>
    </section>
  );
}
