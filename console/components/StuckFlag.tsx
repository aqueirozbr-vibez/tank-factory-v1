"use client";

// The stuck-run flag (ui-context.md · Component Library; PRD §Capability 5 · Stuck runs): a
// running phase with no event for 15 minutes is flagged on the Overview; the count comes from
// the `stuck_runs_7d` indicator. The pulse is the one animation besides the tail tint and it
// stops under prefers-reduced-motion.

import styles from "./StuckFlag.module.css";

export type StuckFlagProps = { count: number };

export function StuckFlag({ count }: StuckFlagProps) {
  const stuck = count > 0;
  return (
    <p
      className={`${styles.flag} ${stuck ? styles.stuck : styles.clear}`}
      role="status"
      aria-live="polite"
      data-stuck={stuck ? "1" : "0"}
    >
      <span className={styles.glyph} aria-hidden="true">
        {stuck ? "!" : "○"}
      </span>
      {stuck ? `${count} stuck run${count === 1 ? "" : "s"} this week` : "No stuck run this week"}
      <span className={`${styles.hint} text-muted`}>no event for 15 min; the timeout kills</span>
    </p>
  );
}
