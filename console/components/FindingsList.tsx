// The scored findings (ui-context.md · Component Library · FindingsList): header "Conformance
// 44 · accept ≥ 70 · round 1 of 2"; columns Severity, Req., Location, Problem, Adjustments,
// Score, Effect; blocking first, then advisory, then dismissed at 60% opacity with the quoted
// Non-goals line; a location opens on GitHub; empty reads "No findings. Every AC met."

import { blobUrl, headerText, orderedFindings, type ReviewView } from "@/lib/review";

import { Chip } from "./Chip";
import styles from "./FindingsList.module.css";

export type FindingsListProps = { review: ReviewView; branchUrl: string | null };

export const EFFECT_TEXT = { blocks: "blocks", advisory: "advisory", dismissed: "dismissed" } as const;

export function FindingsList({ review, branchUrl }: FindingsListProps) {
  const rows = orderedFindings(review);
  return (
    <section aria-labelledby="findings-title" className={styles.section}>
      <div className={styles.head}>
        <h2 id="findings-title" className={styles.heading}>
          Findings <span className="mono text-muted">{rows.length}</span>
        </h2>
        <span className={`${styles.summary} mono text-muted`}>{headerText(review)}</span>
      </div>
      {rows.length === 0 ? (
        <p className="text-muted">No findings. Every AC met.</p>
      ) : (
        <table className={`table ${styles.table}`}>
          <thead>
            <tr>
              <th scope="col">Severity</th>
              <th scope="col">Req.</th>
              <th scope="col">Location</th>
              <th scope="col">Problem</th>
              <th scope="col">Adjustments</th>
              <th scope="col" className={styles.num}>
                Score
              </th>
              <th scope="col">Effect</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((finding, index) => {
              const href = branchUrl ? blobUrl(branchUrl, finding.location) : null;
              return (
                <tr key={`${finding.requirement_id}-${index}`} className={styles[finding.effect]} data-effect={finding.effect} data-row tabIndex={0}>
                  <td>
                    <Chip kind="severity" value={finding.severity} {...(finding.tier !== finding.severity ? { title: `tier ${finding.tier}` } : {})} />
                  </td>
                  <td className="mono">{finding.requirement_id}</td>
                  <td className="mono">
                    {finding.location ? (
                      href ? (
                        <a href={href} target="_blank" rel="noreferrer">
                          {finding.location} ↗
                        </a>
                      ) : (
                        finding.location
                      )
                    ) : (
                      <span className="text-muted">no location</span>
                    )}
                  </td>
                  <td className={styles.problem}>
                    {finding.problem}
                    {finding.dismissed_by ? (
                      <q className={styles.quote} data-testid="non-goals-line">
                        {finding.dismissed_by}
                      </q>
                    ) : null}
                  </td>
                  <td className={`${styles.adjust} mono`}>
                    {finding.equation}
                    {finding.notes.length > 0 ? <span className={`${styles.notes} text-muted`}>{finding.notes.join(" · ")}</span> : null}
                  </td>
                  <td className={`${styles.num} mono`}>{finding.score}</td>
                  <td className="mono">{EFFECT_TEXT[finding.effect]}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
