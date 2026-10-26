import Link from "next/link";

import { AcTable } from "@/components/AcTable";
import { FindingsList } from "@/components/FindingsList";
import { ReviewHeader } from "@/components/ReviewHeader";
import { RoundSwitcher } from "@/components/RoundSwitcher";
import { ScoreBreakdown } from "@/components/ScoreBreakdown";
import { Shell } from "@/components/Shell";
import { loadReview, parseRound, reviewProps } from "@/lib/review";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

// One task's review phase (TRD §5.2 Review findings; ui-context.md · Layout Patterns):
// ReviewHeader, AcTable (one met verdict per AC with evidence), FindingsList, ScoreBreakdown
// (review) and RoundSwitcher; the round is URL state. Server component.
export default async function ReviewPage({
  params,
  searchParams,
}: {
  params: Promise<{ taskId: string }>;
  searchParams: Promise<{ round?: string | string[] }>;
}) {
  const { taskId: raw } = await params;
  const taskId = decodeURIComponent(raw);
  const round = parseRound((await searchParams).round);
  const data = await loadReview(taskId, round, null);
  const branchUrl = data.detail?.links.branch ?? null;
  const taskHref = `/tasks/${encodeURIComponent(taskId)}`;
  return (
    <Shell title="Review findings" current="/tasks/review">
      {data.errors.length > 0 ? (
        <p className={styles.errors} role="status">
          Some reads failed: {data.errors.join("; ")}
        </p>
      ) : null}
      {data.notFound ? (
        <p className={styles.errors} role="status">
          No task {taskId}.
        </p>
      ) : data.pending ? (
        <p className={styles.empty} role="status">
          No review yet: <Link href={taskHref}>{taskId}</Link> has not reached the review phase.
        </p>
      ) : data.badRound ? (
        <p className={styles.errors} role="status">
          There is no round {round} for <Link href={taskHref}>{taskId}</Link>.{" "}
          <Link href={`${taskHref}/review`}>Show the latest round</Link>.
        </p>
      ) : data.review ? (
        <>
          <div className={styles.top}>
            <RoundSwitcher taskId={taskId} round={data.review.round} rounds={data.review.rounds} />
          </div>
          <ReviewHeader review={data.review} detail={data.detail} taskId={taskId} />
          <div className={styles.columns}>
            <div className={styles.main}>
              <AcTable review={data.review} branchUrl={branchUrl} />
              <FindingsList review={data.review} branchUrl={branchUrl} />
            </div>
            <aside className={styles.rail} aria-label="Review score">
              <ScoreBreakdown {...reviewProps(data.review)} />
            </aside>
          </div>
        </>
      ) : null}
    </Shell>
  );
}
