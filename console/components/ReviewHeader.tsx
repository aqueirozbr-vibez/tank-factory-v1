// The review's headline (ui-context.md · Layout Patterns · Review findings): the task, the
// verdict chip, "Conformance 44 · accept ≥ 70 · round 1 of 2", the reviewer's own verdict when
// the policy changed it, the human reasons, the audit override. Server component.

import Link from "next/link";

import type { TaskDetail } from "@/lib/task";
import { headerText, type ReviewView } from "@/lib/review";

import { Chip } from "./Chip";
import styles from "./ReviewHeader.module.css";

export type ReviewHeaderProps = { review: ReviewView; detail: TaskDetail | null; taskId: string };

export function ReviewHeader({ review, detail, taskId }: ReviewHeaderProps) {
  const changed = review.reviewer_verdict !== null && review.reviewer_verdict !== review.verdict;
  return (
    <header className={styles.header}>
      <div className={styles.row}>
        <Link href={`/tasks/${encodeURIComponent(taskId)}`} className={`${styles.task} mono`}>
          {taskId}
        </Link>
        {detail?.task.title ? <span className={styles.title}>{detail.task.title}</span> : null}
        {detail?.links.pr ? (
          <a href={detail.links.pr} target="_blank" rel="noreferrer" className={styles.link}>
            PR #{detail.task.pr_number} ↗
          </a>
        ) : null}
      </div>
      <div className={styles.row}>
        <Chip kind="verdict" value={review.verdict} />
        <span className={`${styles.summary} mono`} data-testid="review-header">
          {headerText(review)}
        </span>
        {changed ? (
          <span className="text-muted">
            reviewer said <span className="mono">{review.reviewer_verdict}</span>; the policy decided
          </span>
        ) : null}
      </div>
      {review.human_reasons.length > 0 ? (
        <p className={styles.human} role="status">
          Human decision: {review.human_reasons.join("; ")}
        </p>
      ) : null}
      {review.missing_ac.length > 0 ? (
        <p className={styles.human} role="status">
          Not addressed by the reviewer: <span className="mono">{review.missing_ac.join(", ")}</span>
        </p>
      ) : null}
      {review.overridden_by ? (
        <p className={`${styles.override} text-muted`}>
          Overridden by <span className="mono">@{review.overridden_by}</span>
          {review.override_reason ? `: ${review.override_reason}` : ""}
        </p>
      ) : null}
    </header>
  );
}
