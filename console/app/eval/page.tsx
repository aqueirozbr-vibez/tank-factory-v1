import { BlockedChange } from "@/components/BlockedChange";
import { EvalSetSummary } from "@/components/EvalSetSummary";
import { InstallDryRun } from "@/components/InstallDryRun";
import { RunEvalButton } from "@/components/RunEvalButton";
import { RunsByVersion } from "@/components/RunsByVersion";
import { Shell } from "@/components/Shell";
import { loadSession } from "@/lib/auth";
import { allowed } from "@/lib/authz";
import { loadEval } from "@/lib/eval";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

// Eval and versions (TRD §5.2 `/eval`; ui-context.md · Layout Patterns · Eval and versions):
// EvalSetSummary, RunsByVersion (EvalComparisonTable), BlockedChange, InstallDryRun,
// RunEvalButton. Server component; the button is the client island.
export default async function EvalPage() {
  const session = await loadSession();
  const caller = session ? { actor: session.login } : null;
  const data = await loadEval(caller);
  const canRun = session !== null && allowed(session.permission, "eval_run");
  const reason = session ? (canRun ? null : `no write access (${session.permission})`) : "sign in to run the eval set";
  return (
    <Shell title="Eval and versions" current="/eval">
      {data.errors.length > 0 ? (
        <p className={styles.errors} role="status">
          Some reads failed: {data.errors.join("; ")}
        </p>
      ) : null}
      <div className={styles.top}>
        <EvalSetSummary sets={data.sets} runs={data.runs} />
        <div className={styles.run}>
          <RunEvalButton sets={data.sets} allowed={canRun} reason={reason} />
        </div>
      </div>
      <div className={styles.columns}>
        <div className={styles.main}>
          <RunsByVersion runs={data.runs} history={data.history} />
          <BlockedChange runs={data.runs} />
        </div>
        <aside className={styles.rail} aria-label="Install dry run and budget">
          <InstallDryRun installation={data.installation} />
        </aside>
      </div>
    </Shell>
  );
}
