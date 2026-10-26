import { ControlsPanel } from "@/components/ControlsPanel";
import { IndicatorsPanel } from "@/components/IndicatorsPanel";
import { KillSwitchDialog } from "@/components/KillSwitchDialog";
import { KpiRow } from "@/components/KpiRow";
import { Meter } from "@/components/Meter";
import { Shell } from "@/components/Shell";
import { StuckFlag } from "@/components/StuckFlag";
import { NeedsHumanTable, RunningQueuedTable, SpendByTask } from "@/components/TaskTables";
import { health } from "@/lib/api";
import { loadSession } from "@/lib/auth";
import { allowed } from "@/lib/authz";
import { loadOverview } from "@/lib/overview";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

// The Overview (TRD §5.2 `/`; ui-context.md · Layout Patterns · Overview): the five headline
// tiles, the needs-a-human and running tables, spend today against the daily cap beside the
// kill-switch, the runtime controls, the collapsed indicators. The kill-switch sits first in the
// content so Tab from the header reaches it (TRD §5.2 keyboard map).
export default async function OverviewPage() {
  const session = await loadSession();
  const caller = session ? { actor: session.login } : null;
  const [data, status] = await Promise.all([loadOverview(caller), health()]);
  const stuck = data.indicators.items.find((m) => m.metric === "stuck_runs_7d")?.value ?? 0;
  const cap = data.installation?.daily_cap_usd ?? 0;
  const spent = data.installation?.spent_today_usd ?? 0;
  const permission = session?.permission ?? null;
  const login = session?.login ?? null;
  const can = (action: string) => permission !== null && allowed(permission, action);
  return (
    <Shell title="Overview" current="/">
      <div className={styles.controls}>
        <KillSwitchDialog
          enabled={status?.factory_enabled ?? data.installation?.factory_enabled ?? null}
          liveProcesses={status?.live_processes ?? null}
          login={login}
          permission={permission}
          allowed={can("kill_switch")}
          last={data.installation?.last_kill_switch ?? null}
        />
      </div>
      {data.errors.length > 0 ? (
        <p className={styles.errors} role="status">
          Some reads failed: {data.errors.join("; ")}
        </p>
      ) : null}
      <KpiRow items={data.kpis.items} day={data.kpis.day} />
      <div className={styles.columns}>
        <div className={styles.main}>
          <NeedsHumanTable plans={data.needsHuman.plans} blocked={data.needsHuman.blocked} />
          <RunningQueuedTable running={data.running} queued={data.queued} />
        </div>
        <aside className={styles.rail} aria-label="Spend and controls">
          <Meter
            label="Spend today"
            used={spent}
            cap={cap}
            state={data.installation ? "default" : "error"}
            {...(data.installation ? { note: `cap N × 3 × $${data.installation.per_task_budget_usd.toFixed(2)}` } : {})}
          />
          <ControlsPanel
            concurrency={data.installation?.concurrency_n ?? null}
            admissionsPaused={data.installation?.admissions_paused ?? null}
            openIncidentSeq={data.installation?.open_incident_seq ?? null}
            login={login}
            permission={permission}
            canSetConcurrency={can("concurrency")}
            canSetAdmissions={can("admissions")}
          />
          <StuckFlag count={Number(stuck)} />
          <SpendByTask running={data.running} />
        </aside>
      </div>
      <IndicatorsPanel items={data.indicators.items} day={data.indicators.day} />
    </Shell>
  );
}
