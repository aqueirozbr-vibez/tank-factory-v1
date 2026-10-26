"use client";

// Concurrency and admissions (PRD Capability 5; TRD §6.1 `POST /controls/concurrency`,
// `POST /controls/admissions`): N between 1 and 10 with a Reason; pause or resume admissions
// with a Reason, clearing the open incident on resume. Every confirming control needs a Reason
// (ui-context.md · Component Library · supporting controls); each is one `events` row with the
// login, written by the orchestrator.

import { useRouter } from "next/navigation";
import { useState } from "react";

import { CONCURRENCY_MAX, CONCURRENCY_MIN, concurrencyValid } from "@/lib/controls";

import styles from "./ControlsPanel.module.css";

export type ControlsPanelProps = {
  concurrency: number | null;
  admissionsPaused: boolean | null;
  openIncidentSeq: number | null;
  login: string | null;
  permission: string | null;
  canSetConcurrency: boolean;
  canSetAdmissions: boolean;
};

type Outcome = { kind: "idle" } | { kind: "sending" } | { kind: "done"; text: string } | { kind: "error"; message: string };

async function post(path: string, body: unknown): Promise<{ ok: boolean; status: number; body: Record<string, unknown> }> {
  const response = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const parsed = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  return { ok: response.ok, status: response.status, body: parsed };
}

export function ControlsPanel(props: ControlsPanelProps) {
  const router = useRouter();
  const [n, setN] = useState(props.concurrency ?? 3);
  const [nReason, setNReason] = useState("");
  const [nState, setNState] = useState<Outcome>({ kind: "idle" });
  const [aReason, setAReason] = useState("");
  const [clearIncident, setClearIncident] = useState(props.openIncidentSeq !== null);
  const [aState, setAState] = useState<Outcome>({ kind: "idle" });
  const why = (allowed: boolean) => (!props.permission ? "sign in to operate the controls" : !allowed ? `no write access (${props.permission})` : null);
  const nWhy = why(props.canSetConcurrency);
  const aWhy = why(props.canSetAdmissions);

  async function setConcurrency() {
    if (!concurrencyValid(n) || !nReason.trim()) return;
    setNState({ kind: "sending" });
    try {
      const result = await post("/api/console/controls/concurrency", { n, reason: nReason.trim() });
      if (!result.ok) {
        setNState({ kind: "error", message: String(result.body["message"] ?? `the console answered ${result.status}`) });
        return;
      }
      setNState({ kind: "done", text: `N ${String(result.body["old"])} → ${String(result.body["new"])} · events row #${String(result.body["event_seq"])} with @${props.login}` });
      setNReason("");
      router.refresh();
    } catch {
      setNState({ kind: "error", message: "the orchestrator is unreachable" });
    }
  }

  async function setAdmissions(paused: boolean) {
    if (!aReason.trim()) return;
    setAState({ kind: "sending" });
    try {
      const body: Record<string, unknown> = { paused: paused ? 1 : 0, reason: aReason.trim() };
      if (!paused && clearIncident && props.openIncidentSeq !== null) body.clear_incident = props.openIncidentSeq;
      const result = await post("/api/console/controls/admissions", body);
      if (!result.ok) {
        setAState({ kind: "error", message: String(result.body["message"] ?? `the console answered ${result.status}`) });
        return;
      }
      setAState({ kind: "done", text: `admissions ${paused ? "paused" : "resumed"} · events row #${String(result.body["event_seq"])} with @${props.login}` });
      setAReason("");
      router.refresh();
    } catch {
      setAState({ kind: "error", message: "the orchestrator is unreachable" });
    }
  }

  return (
    <section className={`card blueprint ${styles.card}`} aria-label="Runtime controls">
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
      <p className="card-kicker mono">Controls</p>

      <form
        className={styles.control}
        aria-label="Concurrency"
        onSubmit={(event) => {
          event.preventDefault();
          void setConcurrency();
        }}
      >
        <div className={styles.head}>
          <span className={styles.label}>Concurrency N</span>
          <span className="mono text-muted">now {props.concurrency ?? "?"}</span>
        </div>
        <div className={styles.fields}>
          <label className={styles.n}>
            <span className="sr-only">New N</span>
            <input className="input" type="number" min={CONCURRENCY_MIN} max={CONCURRENCY_MAX} step={1} value={n} disabled={nWhy !== null} onChange={(event) => setN(Number(event.target.value))} />
          </label>
          <label className={styles.reason}>
            <span className="sr-only">Reason</span>
            <input className="input" type="text" placeholder="Reason (required)" maxLength={500} value={nReason} disabled={nWhy !== null} onChange={(event) => setNReason(event.target.value)} />
          </label>
          <button type="submit" className="btn btn-secondary" disabled={nWhy !== null || !concurrencyValid(n) || !nReason.trim() || n === props.concurrency || nState.kind === "sending"} title={nWhy ?? undefined}>
            {nState.kind === "sending" ? "Setting…" : "Set N"}
          </button>
        </div>
        {nWhy ? <span className={`${styles.why} text-muted`}>{nWhy}</span> : null}
        {nState.kind === "done" ? (
          <span className={styles.ok} role="status">
            {nState.text}
          </span>
        ) : null}
        {nState.kind === "error" ? (
          <span className={styles.error} role="alert">
            {nState.message}
          </span>
        ) : null}
      </form>

      <form
        className={styles.control}
        aria-label="Admissions"
        onSubmit={(event) => {
          event.preventDefault();
          void setAdmissions(!(props.admissionsPaused ?? false));
        }}
      >
        <div className={styles.head}>
          <span className={styles.label}>Admissions</span>
          <span className="mono text-muted">{props.admissionsPaused === null ? "?" : props.admissionsPaused ? "paused" : "open"}</span>
          {props.openIncidentSeq !== null ? (
            <span className={`${styles.incident} mono`} data-testid="open-incident">
              incident #{props.openIncidentSeq} open
            </span>
          ) : null}
        </div>
        <div className={styles.fields}>
          <label className={styles.reason}>
            <span className="sr-only">Reason</span>
            <input className="input" type="text" placeholder="Reason (required)" maxLength={500} value={aReason} disabled={aWhy !== null} onChange={(event) => setAReason(event.target.value)} />
          </label>
          {props.admissionsPaused && props.openIncidentSeq !== null ? (
            <label className={`${styles.clear} mono`}>
              <input type="checkbox" checked={clearIncident} onChange={(event) => setClearIncident(event.target.checked)} /> clear incident #{props.openIncidentSeq}
            </label>
          ) : null}
          <button type="submit" className="btn btn-secondary" disabled={aWhy !== null || !aReason.trim() || props.admissionsPaused === null || aState.kind === "sending"} title={aWhy ?? undefined}>
            {aState.kind === "sending" ? "Recording…" : props.admissionsPaused ? "Resume admissions" : "Pause admissions"}
          </button>
        </div>
        {aWhy ? <span className={`${styles.why} text-muted`}>{aWhy}</span> : null}
        {aState.kind === "done" ? (
          <span className={styles.ok} role="status">
            {aState.text}
          </span>
        ) : null}
        {aState.kind === "error" ? (
          <span className={styles.error} role="alert">
            {aState.message}
          </span>
        ) : null}
      </form>
    </section>
  );
}
