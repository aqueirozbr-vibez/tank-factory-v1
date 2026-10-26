// One row per phase on a shared time axis with duration and cost (ui-context.md · Component
// Library · PhaseTimeline): solid accent = agent phase, hairline outline = code step, hatched =
// waiting on a human; the left glyph carries the phase's result; a running phase extends to now.

import { durationMs, formatDuration, type PhaseView } from "@/lib/task";

import styles from "./PhaseTimeline.module.css";

export type PhaseTimelineProps = { phases: PhaseView[]; queuedAhead?: number | null; now?: Date };

const GLYPH: Record<string, string> = {
  pass: "✓",
  fail: "×",
  running: "●",
  skipped: "–",
};

export function axis(phases: PhaseView[], now: Date): { start: number; end: number } {
  if (phases.length === 0) return { start: now.getTime(), end: now.getTime() + 1 };
  const start = Math.min(...phases.map((p) => new Date(p.started_at).getTime()));
  const end = Math.max(now.getTime(), ...phases.map((p) => (p.ended_at ? new Date(p.ended_at).getTime() : now.getTime())));
  return { start, end: Math.max(end, start + 1000) };
}

export function PhaseTimeline({ phases, queuedAhead = null, now = new Date() }: PhaseTimelineProps) {
  const { start, end } = axis(phases, now);
  const span = end - start;
  return (
    <section aria-labelledby="timeline-title" className={styles.timeline}>
      <h2 id="timeline-title" className={styles.heading}>
        Phases <span className="mono text-muted">{phases.length}</span>
      </h2>
      {phases.length === 0 ? (
        <p className="text-muted">
          {queuedAhead !== null ? `Queued, ${queuedAhead} ahead` : "No phase has run yet."}
        </p>
      ) : (
        <ol className={styles.rows}>
          {phases.map((phase) => {
            const left = ((new Date(phase.started_at).getTime() - start) / span) * 100;
            const width = Math.max(0.5, (durationMs(phase.started_at, phase.ended_at, now) / span) * 100);
            const kind = phase.kind === "agent" ? styles.agent : phase.kind === "engineer" ? styles.human : styles.code;
            const meta = [
              formatDuration(durationMs(phase.started_at, phase.ended_at, now)),
              phase.cost_usd ? `$${phase.cost_usd.toFixed(2)}` : null,
              phase.harness ? `${phase.harness}${phase.model_id ? ` ${phase.model_id}` : ""}` : null,
            ]
              .filter(Boolean)
              .join(" · ");
            return (
              <li key={phase.phase_id} className={styles.row}>
                <span className={`${styles.glyph} mono`} aria-hidden="true">
                  {GLYPH[phase.status] ?? "○"}
                </span>
                <span className={`${styles.name} mono`}>
                  {phase.name}
                  {phase.attempt > 1 ? ` · attempt ${phase.attempt}` : ""}
                </span>
                <span className={styles.track} aria-hidden="true">
                  <span className={`${styles.bar} ${kind}`} style={{ left: `${left}%`, width: `${width}%` }} />
                </span>
                <span className={`${styles.meta} mono`} title={meta}>
                  {meta}
                </span>
                <span className="sr-only">
                  {phase.name} {phase.status}, {formatDuration(durationMs(phase.started_at, phase.ended_at, now))}
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
