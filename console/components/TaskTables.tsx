// The Overview tables (ui-context.md · Layout Patterns · Overview; Spacing and Density):
// "Needs a human" (blocked tasks and plans waiting), "Running and queued", spend by task.
// Server components over trace rows; 36 px rows, ids and numbers right-aligned in mono.

import Link from "next/link";

import type { PlanQueueItem, TaskSummary } from "@/lib/overview";

import { Chip } from "./Chip";
import { Meter } from "./Meter";
import styles from "./TaskTables.module.css";

function taskHref(taskId: string): string {
  return `/tasks/${encodeURIComponent(taskId)}`;
}

function since(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

export type NeedsHumanTableProps = { plans: PlanQueueItem[]; blocked: TaskSummary[] };

export function NeedsHumanTable({ plans, blocked }: NeedsHumanTableProps) {
  const rows = [
    ...plans.map((p) => ({
      key: `plan:${p.task_id}`,
      taskId: p.task_id,
      issue: p.issue_number,
      title: p.title,
      what: "plan review",
      risk: p.risk,
      since: p.waiting_since,
      href: "/approvals?tab=plans",
    })),
    ...blocked.map((t) => ({
      key: `blocked:${t.task_id}`,
      taskId: t.task_id,
      issue: t.issue_number,
      title: t.title,
      what: t.blocked_reason ?? "blocked",
      risk: t.risk,
      since: t.updated_at,
      href: t.blocked_reason === "open_questions" ? "/approvals?tab=questions" : taskHref(t.task_id),
    })),
  ];
  return (
    <section aria-labelledby="needs-human-title" className={styles.section}>
      <h2 id="needs-human-title" className={styles.heading}>
        Needs a human <span className="mono text-muted">{rows.length}</span>
      </h2>
      {rows.length === 0 ? (
        <p className="text-muted">Nothing waits on a person.</p>
      ) : (
        <table className={`table ${styles.table}`}>
          <thead>
            <tr>
              <th scope="col">Task</th>
              <th scope="col">Title</th>
              <th scope="col">Waiting on</th>
              <th scope="col">Risk</th>
              <th scope="col" className={styles.num}>
                Since
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.key} data-row tabIndex={0}>
                <td className="mono">
                  <Link href={row.href}>#{row.issue}</Link>
                </td>
                <td>{row.title ?? <span className="text-muted">untitled</span>}</td>
                <td className="mono">{row.what}</td>
                <td>{row.risk ? <Chip kind="risk" value={row.risk} /> : <span className="text-muted">–</span>}</td>
                <td className={`${styles.num} mono`} title={row.since}>
                  {since(row.since)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export type RunningQueuedTableProps = { running: TaskSummary[]; queued: TaskSummary[] };

export function RunningQueuedTable({ running, queued }: RunningQueuedTableProps) {
  const rows = [...running, ...queued];
  return (
    <section aria-labelledby="running-title" className={styles.section}>
      <h2 id="running-title" className={styles.heading}>
        Running and queued{" "}
        <span className="mono text-muted">
          {running.length} · {queued.length}
        </span>
      </h2>
      {rows.length === 0 ? (
        <p className="text-muted">No task is running or queued.</p>
      ) : (
        <table className={`table ${styles.table}`}>
          <thead>
            <tr>
              <th scope="col">Task</th>
              <th scope="col">Title</th>
              <th scope="col">State</th>
              <th scope="col">Risk</th>
              <th scope="col" className={styles.num}>
                Spent
              </th>
              <th scope="col" className={styles.num}>
                Attempt
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((task, index) => (
              <tr key={task.task_id} data-row tabIndex={0}>
                <td className="mono">
                  <Link href={taskHref(task.task_id)}>#{task.issue_number}</Link>
                </td>
                <td>{task.title ?? <span className="text-muted">untitled</span>}</td>
                <td>
                  <Chip kind="status" value={task.status} />
                  {task.status === "queued" ? (
                    <span className={`${styles.queueNote} text-muted`}>
                      Queued, {index - running.length} ahead
                    </span>
                  ) : null}
                </td>
                <td>{task.risk ? <Chip kind="risk" value={task.risk} /> : <span className="text-muted">–</span>}</td>
                <td className={`${styles.num} mono`}>
                  ${task.spent_usd.toFixed(2)} of ${task.budget_usd.toFixed(2)}
                </td>
                <td className={`${styles.num} mono`}>{task.attempt}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export type SpendByTaskProps = { running: TaskSummary[] };

export function SpendByTask({ running }: SpendByTaskProps) {
  return (
    <section aria-labelledby="spend-title" className={styles.section}>
      <h2 id="spend-title" className={styles.heading}>
        Spend by running task
      </h2>
      {running.length === 0 ? (
        <p className="text-muted">No running task.</p>
      ) : (
        <div className={styles.meters}>
          {running.map((task) => (
            <Meter key={task.task_id} label={`#${task.issue_number}`} used={task.spent_usd} cap={task.budget_usd} />
          ))}
        </div>
      )}
    </section>
  );
}
