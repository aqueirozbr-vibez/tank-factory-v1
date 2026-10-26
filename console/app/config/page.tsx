import Link from "next/link";

import { BudgetsTable, InstallationPanel, InstrumentTables, ProtectedPathsTable, RosterTable } from "@/components/ConfigTables";
import { OpenChanges } from "@/components/OpenChanges";
import { ProposeChangeDialog } from "@/components/ProposeChangeDialog";
import { Shell } from "@/components/Shell";
import { loadSession, userToken } from "@/lib/auth";
import { allowed } from "@/lib/authz";
import { TABS, filesAtMain, loadConfig, openChanges, parseTab, rosterRows, type FactoryFile, type OpenChange, type Tab } from "@/lib/config";
import { fileAtRef, type Repo } from "@/lib/github";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

const TAB_LABELS: Record<Tab, string> = {
  roster: "Roster",
  instruments: "Instruments",
  budgets: "Budgets",
  paths: "Protected paths",
  installation: "Installation",
};

// Configuration (TRD §5.2 `/config?tab=roster|instruments|budgets|paths|installation`;
// ui-context.md · Layout Patterns · Configuration): the open `.factory/` changes on top, the
// tables from the loaded configuration version, and ProposeChangeDialog opening a PR under the
// user's token. Server component; the dialog is the client island.
export default async function ConfigPage({ searchParams }: { searchParams: Promise<{ tab?: string | string[] }> }) {
  const tab = parseTab((await searchParams).tab);
  const session = await loadSession();
  const caller = session ? { actor: session.login } : null;
  const data = await loadConfig(caller);
  const errors = [...data.errors];
  const repo: Repo | null = data.installation ? { owner: data.installation.org, repo: data.installation.repo } : null;
  const canPropose = session !== null && allowed(session.permission, "config_pr");
  const proposeReason = session ? (canPropose ? null : `no write access (${session.permission})`) : "sign in to propose a change";

  let changes: OpenChange[] = [];
  let changesNote: string | null = session ? null : "Sign in to see the open `.factory/` changes under your GitHub access.";
  let files: Array<{ file: FactoryFile; text: string | null }> = [];
  if (session && repo) {
    try {
      const token = await userToken();
      if (token) {
        [changes, files] = await Promise.all([openChanges(token, repo, data.runs), filesAtMain(token, repo, fileAtRef)]);
      }
    } catch (error) {
      changesNote = `GitHub did not answer for the open changes: ${error instanceof Error ? error.message : "unknown error"}`;
    }
  }
  const roster = rosterRows(data.config?.roster, data.config?.vendors ?? {});

  return (
    <Shell title="Configuration" current="/config">
      {errors.length > 0 ? (
        <p className={styles.errors} role="status">
          Some reads failed: {errors.join("; ")}
        </p>
      ) : null}
      <OpenChanges changes={changes} runs={data.runs} note={changesNote} />
      <div className={styles.toolbar}>
        <nav aria-label="Configuration tabs" className={styles.tabs}>
          {TABS.map((t) => (
            <Link key={t} href={`/config?tab=${t}`} className={styles.tab} aria-current={t === tab ? "page" : undefined}>
              {TAB_LABELS[t]}
            </Link>
          ))}
        </nav>
        <ProposeChangeDialog files={files} allowed={canPropose} reason={proposeReason} />
      </div>
      {tab === "roster" ? <RosterTable rows={roster} /> : null}
      {tab === "instruments" ? <InstrumentTables config={data.config} /> : null}
      {tab === "budgets" ? <BudgetsTable installation={data.installation} rows={roster} /> : null}
      {tab === "paths" ? <ProtectedPathsTable paths={data.config?.protected_paths ?? []} /> : null}
      {tab === "installation" ? <InstallationPanel installation={data.installation} config={data.config} /> : null}
    </Shell>
  );
}
