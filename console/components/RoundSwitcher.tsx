// The review rounds as URL state (code-standards · Next.js: screen state lives in `?round=`):
// one link per round, the shown round marked current. Server component; no client state.

import Link from "next/link";

import styles from "./RoundSwitcher.module.css";

export type RoundSwitcherProps = { taskId: string; round: number; rounds: number };

export function RoundSwitcher({ taskId, round, rounds }: RoundSwitcherProps) {
  const base = `/tasks/${encodeURIComponent(taskId)}/review`;
  return (
    <nav aria-label="Review rounds" className={styles.nav}>
      <span className={`${styles.label} mono text-muted`}>round</span>
      {Array.from({ length: Math.max(rounds, 1) }, (_, index) => index + 1).map((n) => (
        <Link
          key={n}
          href={`${base}?round=${n}`}
          className={`${styles.round} mono`}
          aria-current={n === round ? "page" : undefined}
        >
          {n}
        </Link>
      ))}
      <span className={`${styles.label} mono text-muted`}>of {rounds}</span>
    </nav>
  );
}
