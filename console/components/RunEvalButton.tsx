"use client";

// Start a manual eval replay on eval/scratch (TRD §6.1 `POST /eval/runs`: 2 an hour, 409 while a
// run is in progress, 422 under 20 items; needs `write`). Posts through the console's BFF; the
// orchestrator writes the control event and answers with the run id.

import { useRouter } from "next/navigation";
import { useState } from "react";

import { MIN_ITEMS, type EvalSet } from "@/lib/eval";

import styles from "./RunEvalButton.module.css";

export type RunEvalButtonProps = { sets: EvalSet[]; allowed: boolean; reason: string | null };

type Phase = { kind: "idle" } | { kind: "starting" } | { kind: "started"; runId: string } | { kind: "error"; message: string };

export function RunEvalButton({ sets, allowed, reason }: RunEvalButtonProps) {
  const router = useRouter();
  const [setId, setSetId] = useState(sets[0]?.id ?? "");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const chosen = sets.find((s) => s.id === setId) ?? null;
  const tooSmall = chosen !== null && chosen.items < MIN_ITEMS;

  async function start() {
    if (!setId) return;
    setPhase({ kind: "starting" });
    try {
      const response = await fetch("/api/console/eval/runs", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ set_id: setId, trigger: "manual" }),
      });
      const body = (await response.json().catch(() => ({}))) as { run_id?: string; message?: string };
      if (!response.ok) {
        setPhase({ kind: "error", message: body.message ?? `the console answered ${response.status}` });
        return;
      }
      setPhase({ kind: "started", runId: body.run_id ?? "?" });
      router.refresh();
    } catch {
      setPhase({ kind: "error", message: "the orchestrator is unreachable" });
    }
  }

  const disabled = !allowed || !setId || tooSmall || phase.kind === "starting";
  const why = !allowed ? reason : tooSmall ? `an eval set needs at least ${MIN_ITEMS} items` : null;
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        void start();
      }}
      aria-label="Run the eval set"
    >
      <label className={styles.pick}>
        <span className="sr-only">Eval set</span>
        <select className="input" value={setId} onChange={(event) => setSetId(event.target.value)} disabled={sets.length === 0}>
          {sets.length === 0 ? <option value="">no eval set</option> : null}
          {sets.map((set) => (
            <option key={set.id} value={set.id}>
              {set.name} · {set.items} items
            </option>
          ))}
        </select>
      </label>
      <button type="submit" className="btn btn-primary" disabled={disabled} title={why ?? undefined}>
        {phase.kind === "starting" ? "Starting…" : "Run eval"}
      </button>
      {why ? <span className={`${styles.why} text-muted`}>{why}</span> : null}
      {phase.kind === "started" ? (
        <span className={styles.ok} role="status">
          started run <span className="mono">{phase.runId}</span> on eval/scratch
        </span>
      ) : null}
      {phase.kind === "error" ? (
        <span className={styles.error} role="alert">
          {phase.message}
        </span>
      ) : null}
    </form>
  );
}
