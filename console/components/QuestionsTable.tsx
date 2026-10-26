// Blocked tasks with the planner's open questions, each in the issue's language (`lang` on the
// cell; ui-context.md · Layout Patterns · Approvals; PRD Capability 2: an open question blocks
// the build until the product engineer answers on the issue). Server component.

import Link from "next/link";

import { waitingText, type QuestionItem } from "@/lib/approvals";

import styles from "./QuestionsTable.module.css";

export type QuestionsTableProps = { items: QuestionItem[]; repoUrl: string | null; now?: Date };

export function langOf(language: string): string {
  return language.toLowerCase().startsWith("pt") ? "pt-BR" : "en";
}

export function QuestionsTable({ items, repoUrl, now = new Date() }: QuestionsTableProps) {
  return (
    <section aria-labelledby="questions-title" className={styles.section}>
      <h2 id="questions-title" className={styles.heading}>
        Open questions <span className="mono text-muted">{items.length}</span>
      </h2>
      <p className={`${styles.hint} text-muted`}>Answer on the issue; the factory resumes when the questions are closed.</p>
      {items.length === 0 ? (
        <p className="text-muted">No task is blocked on a question.</p>
      ) : (
        <table className={`table ${styles.table}`}>
          <thead>
            <tr>
              <th scope="col">Task</th>
              <th scope="col">Questions</th>
              <th scope="col">Blocked for</th>
              <th scope="col">Answer</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.task_id} data-row tabIndex={0}>
                <td>
                  <Link href={`/tasks/${encodeURIComponent(item.task_id)}`} className="mono">
                    {item.task_id}
                  </Link>
                  <div className={styles.title}>{item.title}</div>
                </td>
                <td lang={langOf(item.language)} className={styles.questions}>
                  {item.questions.length === 0 ? (
                    <span className="text-muted">(the question text was not recorded)</span>
                  ) : (
                    <ol className={styles.list}>
                      {item.questions.map((question, index) => (
                        <li key={index}>{question}</li>
                      ))}
                    </ol>
                  )}
                </td>
                <td className="mono">{waitingText(item.blocked_at, now)}</td>
                <td>
                  {repoUrl ? (
                    <a href={`${repoUrl}/issues/${item.issue_number}`} target="_blank" rel="noreferrer" className="mono">
                      #{item.issue_number} ↗
                    </a>
                  ) : (
                    <span className="mono">#{item.issue_number}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
