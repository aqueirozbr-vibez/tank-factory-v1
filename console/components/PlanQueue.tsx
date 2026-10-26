// The plan-review queue (PRD Capability 2; ui-context.md · Layout Patterns · Approvals): one
// block per plan waiting on `factory:plan-approved`, with the spec, the AC→holdout table, the
// risk breakdown, the approval card and, for a risk:high plan, the raise-to-governed dialog.
// Server component; the card and the dialog are client islands.

import Link from "next/link";

import { approvalState, acHoldoutRows, waitingText, type PlanItem, type Viewer } from "@/lib/approvals";

import { ApprovalCard } from "./ApprovalCard";
import { Chip } from "./Chip";
import styles from "./PlanQueue.module.css";
import { RiskOverrideDialog } from "./RiskOverrideDialog";
import { SpecView } from "./SpecView";

export type PlanQueueProps = {
  items: PlanItem[];
  viewer: Viewer;
  repo: { owner: string; repo: string } | null;
  codeowner: Map<string, boolean>; // governed path → the viewer owns it
  canOverride: boolean;
  now?: Date;
};

export function PlanQueue({ items, viewer, repo, codeowner, canOverride, now = new Date() }: PlanQueueProps) {
  const base = repo ? `https://github.com/${repo.owner}/${repo.repo}` : null;
  return (
    <section aria-labelledby="plans-title" className={styles.queue}>
      <h2 id="plans-title" className={styles.heading}>
        Plans waiting <span className="mono text-muted">{items.length}</span>
      </h2>
      {items.length === 0 ? (
        <p className="text-muted">No plan is waiting for approval.</p>
      ) : (
        <ol className={styles.list}>
          {items.map((item, index) => {
            const governedPath = item.governed_path ?? item.spec_path;
            const state = approvalState(item, viewer, governedPath ? (codeowner.get(governedPath) ?? null) : null);
            const rows = acHoldoutRows(item.ac_holdout);
            const branch = base ? `${base}/tree/agent/${item.issue_number}` : null;
            return (
              <li key={item.task_id} className={styles.item} data-row tabIndex={0}>
                <header className={styles.head}>
                  <Link href={`/tasks/${encodeURIComponent(item.task_id)}`} className={`${styles.task} mono`}>
                    {item.task_id}
                  </Link>
                  <span className={styles.title}>{item.title ?? <span className="text-muted">untitled</span>}</span>
                  {item.risk ? <Chip kind="risk" value={item.risk} /> : <Chip kind="risk" value="unclassified" />}
                  {item.risk_score !== null ? <span className="mono text-muted">score {item.risk_score}</span> : null}
                  <span className={`${styles.waiting} mono text-muted`}>waiting {waitingText(item.waiting_since, now)}</span>
                  {canOverride && item.risk === "risk:high" ? (
                    <RiskOverrideDialog taskId={item.task_id} current={item.risk} />
                  ) : null}
                </header>
                <div className={styles.columns}>
                  <SpecView item={item} specUrl={base && item.spec_path ? `${base}/blob/agent/${item.issue_number}/${item.spec_path}` : null} />
                  <ApprovalCard
                    taskId={item.task_id}
                    issue={item.issue_number}
                    repo={repo ? `${repo.owner}/${repo.repo}` : "repository"}
                    login={viewer?.login ?? null}
                    specSha={item.spec_sha}
                    holdout={{ present: rows.filter((r) => r.ok).length, total: rows.length }}
                    state={state}
                    issueUrl={base ? `${base}/issues/${item.issue_number}` : "#"}
                    focusKey={index === 0}
                  />
                </div>
                {branch ? (
                  <a href={branch} target="_blank" rel="noreferrer" className={`${styles.branch} mono`}>
                    agent/{item.issue_number} ↗
                  </a>
                ) : null}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
