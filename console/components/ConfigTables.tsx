// The configuration tables (ui-context.md · Layout Patterns · Configuration; PRD Capability 6:
// roster, both instruments, budgets and caps, protected paths, installation settings). Server
// components over the loaded configuration version; changes go through ProposeChangeDialog.

import type { ConfigView, Installation, RoleRow } from "@/lib/config";
import { pathKind, qualityRows } from "@/lib/config";

import styles from "./ConfigTables.module.css";

export function RosterTable({ rows }: { rows: RoleRow[] }) {
  return (
    <section aria-labelledby="roster-title" className={styles.section}>
      <h2 id="roster-title" className={styles.heading}>
        Roster
      </h2>
      <p className={`${styles.note} text-muted`}>Two vendors stay in the roster: the builder and the reviewer never share one (PRD Capability 4).</p>
      {rows.length === 0 ? (
        <p className="text-muted">The roster is not readable.</p>
      ) : (
        <table className={`table ${styles.table}`}>
          <thead>
            <tr>
              <th scope="col">Role</th>
              <th scope="col">Harness</th>
              <th scope="col">Vendor</th>
              <th scope="col">Model</th>
              <th scope="col">Thinking</th>
              <th scope="col">Tools</th>
              <th scope="col">Writes</th>
              <th scope="col" className={styles.num}>
                Max turns
              </th>
              <th scope="col" className={styles.num}>
                Phase cap
              </th>
              <th scope="col" className={styles.num}>
                Timeout
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.role} data-role={row.role}>
                <th scope="row" className="mono">
                  {row.role}
                </th>
                <td className="mono">{row.harness}</td>
                <td className="mono">{row.vendor ?? "—"}</td>
                <td className="mono">{row.model}</td>
                <td className="mono">{row.thinking ?? "—"}</td>
                <td className="mono">{row.tools.join(", ") || "—"}</td>
                <td className="mono">{row.writes.join(", ") || "none"}</td>
                <td className={`${styles.num} mono`}>{row.max_turns ?? "—"}</td>
                <td className={`${styles.num} mono`}>${row.phase_cap_usd.toFixed(2)}</td>
                <td className={`${styles.num} mono`}>{row.timeout_s} s</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function InstrumentTables({ config }: { config: ConfigView | null }) {
  return (
    <section aria-labelledby="instruments-title" className={styles.section}>
      <h2 id="instruments-title" className={styles.heading}>
        Instruments
      </h2>
      <p className={`${styles.note} text-muted`}>
        The risk and review instruments are the tech lead&apos;s class of change: proposed as a `.factory/` PR, gated on the eval set and
        reviewed by a CODEOWNER before they load.
      </p>
      {config ? (
        <div className={styles.instruments}>
          <details open>
            <summary className={styles.summary}>
              <span className="mono">.factory/risk.yaml</span>
            </summary>
            <pre className={`${styles.yaml} mono`} tabIndex={0}>{config.risk_yaml || "(empty)"}</pre>
          </details>
          <details open>
            <summary className={styles.summary}>
              <span className="mono">.factory/review-policy.yaml</span>
            </summary>
            <pre className={`${styles.yaml} mono`} tabIndex={0}>{config.review_policy_yaml || "(empty)"}</pre>
          </details>
          <details>
            <summary className={styles.summary}>
              <span className="mono">.factory/quality.yaml</span> <span className="text-muted">gate commands</span>
            </summary>
            <table className={`table ${styles.table}`}>
              <tbody>
                {qualityRows(config.quality_commands).map((row) => (
                  <tr key={row.gate}>
                    <th scope="row" className="mono">
                      {row.gate}
                    </th>
                    <td className="mono">{row.argv.join(" ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </div>
      ) : (
        <p className="text-muted">The configuration is not readable.</p>
      )}
    </section>
  );
}

export function BudgetsTable({ installation, rows }: { installation: Installation | null; rows: RoleRow[] }) {
  return (
    <section aria-labelledby="budgets-title" className={styles.section}>
      <h2 id="budgets-title" className={styles.heading}>
        Budgets and caps
      </h2>
      {installation ? (
        <dl className={styles.facts}>
          <dt>Per-task budget</dt>
          <dd className="mono">
            ${installation.per_task_budget_usd.toFixed(2)} · mode {installation.budget_mode}
          </dd>
          <dt>Daily cap</dt>
          <dd className="mono">
            ${installation.daily_cap_usd.toFixed(2)} = N {installation.concurrency_n} × 3 × ${installation.per_task_budget_usd.toFixed(2)}
          </dd>
          <dt>Concurrency N</dt>
          <dd className="mono">{installation.concurrency_n}</dd>
        </dl>
      ) : (
        <p className="text-muted">The installation is not readable.</p>
      )}
      <table className={`table ${styles.table}`} aria-label="Phase caps by role">
        <thead>
          <tr>
            <th scope="col">Role</th>
            <th scope="col" className={styles.num}>
              Phase cap
            </th>
            <th scope="col" className={styles.num}>
              Timeout
            </th>
            <th scope="col" className={styles.num}>
              Max turns
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.role}>
              <th scope="row" className="mono">
                {row.role}
              </th>
              <td className={`${styles.num} mono`}>${row.phase_cap_usd.toFixed(2)}</td>
              <td className={`${styles.num} mono`}>{row.timeout_s} s</td>
              <td className={`${styles.num} mono`}>{row.max_turns ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className={`${styles.note} text-muted`}>
        A phase is refused when spent + cap would pass the task budget; every cap breach SIGTERMs within 60 s (TRD §6.3).
      </p>
    </section>
  );
}

export function ProtectedPathsTable({ paths }: { paths: string[] }) {
  return (
    <section aria-labelledby="paths-title" className={styles.section}>
      <h2 id="paths-title" className={styles.heading}>
        Protected paths <span className="mono text-muted">{paths.length}</span>
      </h2>
      <p className={`${styles.note} text-muted`}>
        Any attempt on a protected path opens an incident; a protected-path change reaching a PR halts admissions (PRD leading indicators).
      </p>
      <table className={`table ${styles.table}`}>
        <thead>
          <tr>
            <th scope="col">Path</th>
            <th scope="col">Set</th>
          </tr>
        </thead>
        <tbody>
          {paths.map((path) => (
            <tr key={path} data-kind={pathKind(path)}>
              <td className="mono">{path}</td>
              <td className="text-muted">{pathKind(path) === "prd" ? "PRD set" : "added in .factory/factory.yaml"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function InstallationPanel({ installation, config }: { installation: Installation | null; config: ConfigView | null }) {
  return (
    <section aria-labelledby="installation-title" className={styles.section}>
      <h2 id="installation-title" className={styles.heading}>
        Installation
      </h2>
      {installation ? (
        <dl className={styles.facts}>
          <dt>Repository</dt>
          <dd className="mono">
            {installation.org}/{installation.repo}
          </dd>
          <dt>Factory version</dt>
          <dd className="mono">{installation.factory_version}</dd>
          <dt>Configuration</dt>
          <dd className="mono">
            {installation.config_sha.slice(0, 12)}
            {config ? ` · main ${config.main_commit.slice(0, 12)} · loaded ${config.loaded_at}` : ""}
          </dd>
          <dt>Timezone</dt>
          <dd className="mono">{installation.timezone}</dd>
          <dt>Retention</dt>
          <dd className="mono">{installation.retention_days} days</dd>
          <dt>Factory enabled</dt>
          <dd className="mono">{installation.factory_enabled ? "1" : "0"}</dd>
          <dt>Admissions</dt>
          <dd className="mono">{installation.admissions_paused ? "paused" : "open"}</dd>
          <dt>Vendors</dt>
          <dd className="mono">{config ? Object.entries(config.vendors).map(([role, vendor]) => `${role}: ${vendor}`).join(" · ") : "—"}</dd>
          <dt>Export key</dt>
          <dd className="mono">{installation.export_pubkey.slice(0, 16)}…</dd>
        </dl>
      ) : (
        <p className="text-muted">The installation is not readable.</p>
      )}
    </section>
  );
}
