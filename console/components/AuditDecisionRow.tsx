"use client";

// One audit decision (PRD Capability 4; TRD §6.1 `POST /audit/{task_id}`): agree or disagree
// with the reviewer's verdict, a Reason always, one decision per task per month. Every
// confirming control needs a Reason (ui-context.md · Component Library · supporting controls).

import { useRouter } from "next/navigation";
import { useId, useState } from "react";

import styles from "./AuditDecisionRow.module.css";

export type AuditDecisionRowProps = { taskId: string; month: string; canDecide: boolean; reason: string | null };

type Phase = { kind: "idle" } | { kind: "sending" } | { kind: "done"; decision: string } | { kind: "error"; message: string };

export function AuditDecisionRow({ taskId, month, canDecide, reason }: AuditDecisionRowProps) {
  const router = useRouter();
  const id = useId();
  const [decision, setDecision] = useState<"agree" | "disagree" | null>(null);
  const [why, setWhy] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  async function record() {
    if (!decision || !why.trim()) return;
    setPhase({ kind: "sending" });
    try {
      const response = await fetch(`/api/console/approvals/audit/${encodeURIComponent(taskId)}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ decision, reason: why.trim(), month }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { message?: string };
        setPhase({ kind: "error", message: body.message ?? `the console answered ${response.status}` });
        return;
      }
      setPhase({ kind: "done", decision });
      router.refresh();
    } catch {
      setPhase({ kind: "error", message: "the orchestrator is unreachable" });
    }
  }

  if (phase.kind === "done") {
    return (
      <p className={styles.done} role="status">
        recorded: <span className="mono">{phase.decision}</span>
      </p>
    );
  }
  const ready = canDecide && decision !== null && why.trim().length > 0 && phase.kind !== "sending";
  return (
    <form
      className={styles.row}
      onSubmit={(event) => {
        event.preventDefault();
        void record();
      }}
      aria-label={`Audit decision for ${taskId}`}
    >
      <fieldset className={styles.choice} disabled={!canDecide}>
        <legend className="sr-only">Decision</legend>
        {(["agree", "disagree"] as const).map((value) => (
          <label key={value} className={`${styles.option} mono`}>
            <input type="radio" name={`${id}-decision`} value={value} checked={decision === value} onChange={() => setDecision(value)} />
            {value}
          </label>
        ))}
      </fieldset>
      <label className={styles.reason}>
        <span className="sr-only">Reason</span>
        <input
          className="input"
          type="text"
          placeholder="Reason (required)"
          value={why}
          maxLength={500}
          disabled={!canDecide}
          onChange={(event) => setWhy(event.target.value)}
        />
      </label>
      <button type="submit" className="btn btn-secondary" disabled={!ready} title={reason ?? undefined}>
        {phase.kind === "sending" ? "Recording…" : "Record"}
      </button>
      {!canDecide && reason ? <span className={`${styles.note} text-muted`}>{reason}</span> : null}
      {phase.kind === "error" ? (
        <span className={styles.error} role="alert">
          {phase.message}
        </span>
      ) : null}
    </form>
  );
}
