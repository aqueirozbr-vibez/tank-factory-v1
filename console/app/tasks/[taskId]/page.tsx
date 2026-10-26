import { BudgetRaiseDialog } from "@/components/BudgetRaiseDialog";
import { ChainFooter } from "@/components/ChainFooter";
import { EnvelopeViewer } from "@/components/EnvelopeViewer";
import { GateLadder } from "@/components/GateLadder";
import { Meter } from "@/components/Meter";
import { PhaseTimeline } from "@/components/PhaseTimeline";
import { RepairRounds } from "@/components/RepairRounds";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";
import { Shell } from "@/components/Shell";
import { TaskStrip } from "@/components/TaskStrip";
import { TraceTable } from "@/components/TraceTable";
import { loadSession } from "@/lib/auth";
import { allowed } from "@/lib/authz";
import { loadTask, riskProps } from "@/lib/task";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

// One task's trace (TRD §5.2 Task detail; ui-context.md · Layout Patterns · Task detail):
// strip, phases, gate ladder with its drawer, repair rounds, task budget meter with the one-time
// raise, risk score, envelopes, the live trace and the chain footer. Server component; the
// islands are client.
export default async function TaskDetailPage({ params }: { params: Promise<{ taskId: string }> }) {
  const { taskId: raw } = await params;
  const taskId = decodeURIComponent(raw);
  const session = await loadSession();
  const data = await loadTask(taskId, session ? { actor: session.login } : null);
  const live = data.detail !== null && ["queued", "running", "blocked"].includes(data.detail.task.status);
  const permission = session?.permission ?? null;
  return (
    <Shell title="Task detail" current="/tasks">
      {data.notFound || data.detail === null ? (
        <p className={styles.errors} role="status">
          {data.notFound ? `No task ${taskId}.` : `The task could not be read: ${data.errors.join("; ")}`}
        </p>
      ) : (
        <>
          {data.errors.length > 0 ? (
            <p className={styles.errors} role="status">
              Some reads failed: {data.errors.join("; ")}
            </p>
          ) : null}
          <TaskStrip detail={data.detail} />
          <div className={styles.section}>
            <PhaseTimeline phases={data.detail.phases} queuedAhead={data.queuedAhead} />
          </div>
          <div className={styles.columns}>
            <div className={styles.main}>
              <GateLadder gates={data.gates} phases={data.detail.phases} />
              <RepairRounds phases={data.detail.phases} gates={data.gates} />
            </div>
            <aside className={styles.rail} aria-label="Budget and risk">
              <Meter
                label="Task budget"
                used={data.detail.budget.spent_usd}
                cap={data.detail.budget.budget_usd}
                {...(data.detail.budget.raised ? { note: "raised once" } : {})}
              />
              <BudgetRaiseDialog
                taskId={taskId}
                current={data.detail.budget.budget_usd}
                raised={data.detail.budget.raised}
                status={data.detail.task.status}
                login={session?.login ?? null}
                permission={permission}
                allowed={permission !== null && allowed(permission, "budget_raise")}
              />
              {data.risk ? (
                <ScoreBreakdown {...riskProps(data.risk, data.detail.task, data.override)} />
              ) : (
                <p className={`${styles.pending} text-muted`}>Risk pending: the plan phase has not classified the Scope.</p>
              )}
            </aside>
          </div>
          <div className={styles.section}>
            <EnvelopeViewer envelopes={data.envelopes} />
          </div>
          <div className={styles.section}>
            <TraceTable
              taskId={taskId}
              initialRows={data.events}
              initialLastSeq={data.lastSeq}
              oldestSeq={data.oldestSeq}
              moreBefore={data.moreBefore}
              live={live}
            />
          </div>
          <ChainFooter chain={data.detail.chain} />
        </>
      )}
    </Shell>
  );
}
