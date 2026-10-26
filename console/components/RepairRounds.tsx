// Repair rounds per build attempt (PRD Capability 3: at most 3 in the same builder session;
// ui-context.md · Layout Patterns · Task detail). Each failed code_gates phase is one round;
// the failing gate is named from its gate_results row.

import { repairRounds, type GateView, type PhaseView } from "@/lib/task";

import styles from "./RepairRounds.module.css";

export type RepairRoundsProps = { phases: PhaseView[]; gates: GateView[] };

export function failingGate(phase: PhaseView, gates: GateView[]): string | null {
  const failed = gates.find((g) => g.phase_id === phase.phase_id && !g.passed);
  return failed ? failed.gate : (phase.error ?? null);
}

export function RepairRounds({ phases, gates }: RepairRoundsProps) {
  const rounds = repairRounds(phases);
  return (
    <section aria-labelledby="repairs-title" className={styles.repairs}>
      <h2 id="repairs-title" className={styles.heading}>
        Repair rounds
      </h2>
      {rounds.length === 0 ? (
        <p className="text-muted">No build attempt yet.</p>
      ) : (
        <ul className={styles.list}>
          {rounds.map((round) => {
            const failures = phases.filter(
              (p) => p.attempt === round.attempt && p.name === "code_gates" && p.status === "fail",
            );
            return (
              <li key={round.attempt} className={styles.round}>
                <span className="mono">
                  build {round.attempt} · repair {round.repairs} of {round.cap}
                </span>
                {failures.length > 0 ? (
                  <span className={`${styles.gates} mono text-muted`}>
                    {failures.map((phase, index) => `round ${index + 1}: ${failingGate(phase, gates) ?? "gate"}`).join(" · ")}
                  </span>
                ) : (
                  <span className="text-muted">no repair needed</span>
                )}
                {round.repairs >= round.cap ? (
                  <span className={styles.cap}>cap reached; the next failure blocks the task</span>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
