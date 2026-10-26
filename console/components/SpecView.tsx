// The spec a plan approval binds to (ui-context.md · Layout Patterns · Approvals: `SpecView`
// with sections and the AC→holdout table with existence checks; PRD Capability 2). Server
// component: the sections as the planner wrote them, the spec gate's AC→holdout checks, the
// risk breakdown the instrument logged.

import { acHoldoutRows, parseSections, type PlanItem } from "@/lib/approvals";
import { riskProps, riskSchema } from "@/lib/task";

import { Chip } from "./Chip";
import { ScoreBreakdown } from "./ScoreBreakdown";
import styles from "./SpecView.module.css";

export type SpecViewProps = { item: PlanItem; specUrl: string | null };

export function SpecView({ item, specUrl }: SpecViewProps) {
  const sections = parseSections(item.spec_text);
  const rows = acHoldoutRows(item.ac_holdout);
  const risk = item.risk_breakdown ? riskSchema.safeParse(item.risk_breakdown) : null;
  const present = rows.filter((r) => r.ok).length;
  return (
    <div className={styles.spec}>
      <div className={styles.head}>
        <span className="mono">{item.spec_path ?? "spec pending"}</span>
        {item.spec_sha ? <span className="mono text-muted">@ {item.spec_sha.slice(0, 12)}</span> : null}
        {specUrl ? (
          <a href={specUrl} target="_blank" rel="noreferrer" className={styles.link}>
            open on GitHub ↗
          </a>
        ) : null}
      </div>
      {sections.length === 0 ? (
        <p className="text-muted">The spec text is not readable from the console; open it on GitHub.</p>
      ) : (
        <div className={styles.sections}>
          {sections.map((section) => (
            <details key={section.title} open={["Outcome", "Scope", "Non-goals", "Acceptance criteria"].includes(section.title)}>
              <summary className={styles.summary}>{section.title}</summary>
              <pre className={`${styles.body} mono`} tabIndex={0}>{section.body || "(empty)"}</pre>
            </details>
          ))}
        </div>
      )}
      <section aria-label="Acceptance criteria to holdout tests" className={styles.holdout}>
        <h4 className={styles.subheading}>
          AC → holdout <span className="mono text-muted">{present} of {rows.length} present</span>
        </h4>
        {rows.length === 0 ? (
          <p className="text-muted">The spec gate recorded no AC→holdout map.</p>
        ) : (
          <table className={`table ${styles.table}`}>
            <thead>
              <tr>
                <th scope="col">AC</th>
                <th scope="col">Holdout test</th>
                <th scope="col">Exists</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.ac} data-ok={row.ok ? "true" : "false"}>
                  <th scope="row" className="mono">
                    {row.ac}
                  </th>
                  <td className={`${styles.note} mono`}>{row.note || <span className="text-muted">none mapped</span>}</td>
                  <td>
                    <Chip kind="gate" value={row.ok ? "pass" : "fail"} title={row.ok ? "present in the holdout checkout" : "missing"} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      {risk?.success ? (
        <ScoreBreakdown {...riskProps(risk.data, { risk: item.risk }, null)} />
      ) : (
        <p className="text-muted">Risk breakdown not recorded.</p>
      )}
    </div>
  );
}
