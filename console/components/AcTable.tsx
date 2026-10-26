// One `met` verdict per acceptance criterion with its evidence (ui-context.md · Layout
// Patterns · Review findings; PRD Capability 4: an AC not met is a Blocker). Server component.

import { acRows, blobUrl, type ReviewView } from "@/lib/review";

import { Chip } from "./Chip";
import styles from "./AcTable.module.css";

export type AcTableProps = { review: ReviewView; branchUrl: string | null };

export function AcTable({ review, branchUrl }: AcTableProps) {
  const rows = acRows(review);
  return (
    <section aria-labelledby="ac-title" className={styles.section}>
      <h2 id="ac-title" className={styles.heading}>
        Acceptance criteria <span className="mono text-muted">{rows.length}</span>
      </h2>
      {rows.length === 0 ? (
        <p className="text-muted">The reviewer named no acceptance criterion.</p>
      ) : (
        <table className={`table ${styles.table}`}>
          <thead>
            <tr>
              <th scope="col">AC</th>
              <th scope="col">Met</th>
              <th scope="col">Evidence</th>
              <th scope="col">Finding</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const href = branchUrl ? blobUrl(branchUrl, row.location) : null;
              return (
                <tr key={row.id} data-met={row.met === null ? "unaddressed" : row.met ? "true" : "false"} data-row tabIndex={0}>
                  <th scope="row" className="mono">
                    {row.id}
                  </th>
                  <td>
                    {row.met === null ? (
                      <span className="text-muted">– not addressed</span>
                    ) : (
                      <Chip kind="gate" value={row.met ? "pass" : "fail"} title={row.met ? "met" : "not met"} />
                    )}
                  </td>
                  <td className={styles.evidence}>{row.evidence || <span className="text-muted">none given</span>}</td>
                  <td>
                    <div className={styles.finding}>
                      {row.severity ? <Chip kind="severity" value={row.severity} /> : null}
                      {row.problem ? <span>{row.problem}</span> : null}
                      {href && row.location ? (
                        <a href={href} target="_blank" rel="noreferrer" className="mono">
                          {row.location} ↗
                        </a>
                      ) : null}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
