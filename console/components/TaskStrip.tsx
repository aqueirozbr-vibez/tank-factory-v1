// The strip under the title (ui-context.md · Layout Patterns · Task detail): status chip, risk
// chip, links to issue, spec, branch and PR. Server component.

import type { TaskDetail } from "@/lib/task";

import { Chip } from "./Chip";
import styles from "./TaskStrip.module.css";

export type TaskStripProps = { detail: TaskDetail };

export function TaskStrip({ detail }: TaskStripProps) {
  const { task, links } = detail;
  return (
    <div className={styles.strip}>
      <span className={`${styles.id} mono`}>{task.task_id}</span>
      <Chip kind="status" value={task.status} {...(task.blocked_reason ? { title: task.blocked_reason } : {})} />
      {task.risk ? <Chip kind="risk" value={task.risk} /> : <span className="text-muted">risk pending</span>}
      <span className={styles.title}>{task.title ?? <span className="text-muted">untitled</span>}</span>
      <nav aria-label="Task links" className={styles.links}>
        <a href={links.issue} target="_blank" rel="noreferrer">
          issue ↗
        </a>
        {links.spec ? (
          <a href={links.spec} target="_blank" rel="noreferrer">
            spec ↗
          </a>
        ) : (
          <span className="text-muted">no spec yet</span>
        )}
        <a href={links.branch} target="_blank" rel="noreferrer">
          branch ↗
        </a>
        {links.pr ? (
          <a href={links.pr} target="_blank" rel="noreferrer">
            PR #{task.pr_number} ↗
          </a>
        ) : (
          <span className="text-muted">no PR yet</span>
        )}
      </nav>
      <span className={`${styles.attempt} mono text-muted`}>attempt {task.attempt}</span>
    </div>
  );
}
