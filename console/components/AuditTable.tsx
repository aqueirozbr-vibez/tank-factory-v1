// The monthly audit (PRD Capability 4: a tech lead audits every high-risk and governed PR and
// 10% of low-risk PRs; ui-context.md · Layout Patterns · Approvals: `AuditStrata` and
// `AuditTable` with `AuditDecisionRow`). Server components; the decision row is a client island.

import Link from "next/link";

import { monthText, strata, type AuditItem } from "@/lib/approvals";

import { AuditDecisionRow } from "./AuditDecisionRow";
import { Chip } from "./Chip";
import styles from "./AuditTable.module.css";

export type AuditStrataProps = { items: AuditItem[]; month: string | null };

export function AuditStrata({ items, month }: AuditStrataProps) {
  return (
    <div className={styles.strata} role="group" aria-label={`Audit strata for ${monthText(month)}`}>
      {strata(items).map((s) => (
        <div key={s.stratum} className={styles.stratum}>
          <Chip kind="risk" value={s.stratum} />
          <span className={`${styles.count} mono`}>{s.waiting}</span>
          <span className="text-muted">waiting · {s.rule}</span>
        </div>
      ))}
    </div>
  );
}

export type AuditTableProps = {
  items: AuditItem[];
  month: string | null;
  repoUrl: string | null;
  canDecide: boolean;
  reason: string | null;
};

export function AuditTable({ items, month, repoUrl, canDecide, reason }: AuditTableProps) {
  return (
    <section aria-labelledby="audit-title" className={styles.section}>
      <h2 id="audit-title" className={styles.heading}>
        Audit queue · {monthText(month)} <span className="mono text-muted">{items.length}</span>
      </h2>
      <AuditStrata items={items} month={month} />
      {items.length === 0 ? (
        <p className="text-muted">Nothing to audit this month: every drawn PR is decided.</p>
      ) : (
        <table className={`table ${styles.table}`}>
          <thead>
            <tr>
              <th scope="col">Stratum</th>
              <th scope="col">Task</th>
              <th scope="col">PR</th>
              <th scope="col">Decision</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} data-stratum={item.stratum} data-row tabIndex={0}>
                <td>
                  <Chip kind="risk" value={item.stratum} />
                </td>
                <td>
                  <Link href={`/tasks/${encodeURIComponent(item.task_id)}/review`} className="mono">
                    {item.task_id}
                  </Link>
                </td>
                <td>
                  {repoUrl ? (
                    <a href={`${repoUrl}/pull/${item.pr}`} target="_blank" rel="noreferrer" className="mono">
                      #{item.pr} ↗
                    </a>
                  ) : (
                    <span className="mono">#{item.pr}</span>
                  )}
                </td>
                <td>
                  <AuditDecisionRow taskId={item.task_id} month={item.month} canDecide={canDecide} reason={reason} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
