"use client";

// The Approvals badge in the nav polls /needs-human/count every 30 s through the console's own
// read proxy (TRD §5.2 NavBadge). The server renders the first value; the island keeps it fresh.

import { useEffect, useState } from "react";

import styles from "./NavBadge.module.css";

export const POLL_MS = 30_000;

export type Counts = { plans: number; questions: number; decisions: number; budget: number };

export function total(counts: Counts | null): number {
  if (!counts) return 0;
  return counts.plans + counts.questions + counts.decisions + counts.budget;
}

export type NavBadgeProps = { initial: Counts | null };

export function NavBadge({ initial }: NavBadgeProps) {
  const [counts, setCounts] = useState<Counts | null>(initial);

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const response = await fetch("/api/console/needs-human", { cache: "no-store" });
        if (!response.ok) return;
        const body = (await response.json()) as Counts;
        if (alive) setCounts(body);
      } catch {
        // the server value stays until the next poll
      }
    }
    const timer = window.setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  const n = total(counts);
  if (n === 0) return null;
  return (
    <span className={styles.badge} aria-label={`${n} items need a human`} data-count={n}>
      {n}
    </span>
  );
}
