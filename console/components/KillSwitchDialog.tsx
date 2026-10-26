"use client";

// The kill-switch (ui-context.md · Component Library · KillSwitchDialog; PRD Capability 5; TRD
// §6.1 `POST /controls/kill-switch`): header pill `FACTORY_ENABLED=1 · 3 live processes`, the
// button "Stop factory…" outlined in the fail role, a dialog that states the effect, requires a
// one-line Reason ("Recorded as an events row with your login @login"), focus in Reason, `Esc`
// cancels, Enter confirms only when Reason is filled, loading "Stopping 3 processes · 41 s left",
// disabled without write access or when already 0 (who and when). The same dialog turns the
// factory back on; re-enabling re-admits nothing.

import * as Dialog from "@radix-ui/react-dialog";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { KILL_SWITCH_BOUND_S, countdownText, killSwitchDisabledReason, pillText, type KillSwitchNote } from "@/lib/controls";

import styles from "./KillSwitchDialog.module.css";

export type KillSwitchDialogProps = {
  enabled: boolean | null;
  liveProcesses: number | null;
  login: string | null;
  permission: string | null;
  allowed: boolean;
  last: KillSwitchNote | null;
};

type Phase =
  | { kind: "idle" }
  | { kind: "stopping"; startedAt: number }
  | { kind: "done"; seq: number; live: number; elapsed: number | null; enabled: boolean }
  | { kind: "error"; message: string };

export function KillSwitchDialog({ enabled, liveProcesses, login, permission, allowed, last }: KillSwitchDialogProps) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [now, setNow] = useState(() => Date.now());
  const turningOff = enabled !== false;
  const disabledReason = turningOff
    ? killSwitchDisabledReason({ allowed, permission, enabled, last })
    : !permission
      ? "sign in to operate the kill-switch"
      : !allowed
        ? `no write access (${permission})`
        : null;

  useEffect(() => {
    if (phase.kind !== "stopping") return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [phase.kind]);

  async function confirm() {
    if (!reason.trim()) return;
    setPhase({ kind: "stopping", startedAt: Date.now() });
    try {
      const response = await fetch("/api/console/controls/kill-switch", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ enabled: turningOff ? 0 : 1, reason: reason.trim() }),
      });
      const body = (await response.json().catch(() => ({}))) as {
        event_seq?: number;
        live_processes?: number;
        elapsed_s?: number;
        factory_enabled?: boolean;
        message?: string;
      };
      if (!response.ok) {
        setPhase({ kind: "error", message: body.message ?? `the console answered ${response.status}` });
        return;
      }
      setPhase({ kind: "done", seq: body.event_seq ?? 0, live: body.live_processes ?? 0, elapsed: body.elapsed_s ?? null, enabled: body.factory_enabled ?? !turningOff });
      router.refresh();
    } catch {
      setPhase({ kind: "error", message: "the orchestrator is unreachable; the flag did not change" });
    }
  }

  const secondsLeft =
    phase.kind === "stopping" ? KILL_SWITCH_BOUND_S - Math.floor((Math.max(now, phase.startedAt) - phase.startedAt) / 1000) : KILL_SWITCH_BOUND_S;
  return (
    <div className={styles.row}>
      <span className={`${styles.pill} mono`} data-state={enabled === null ? "unknown" : enabled ? "on" : "off"} data-testid="kill-switch-pill">
        {pillText(enabled, liveProcesses)}
      </span>
      <Dialog.Root
        open={open}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) {
            setPhase({ kind: "idle" });
            setReason("");
          }
        }}
      >
        <Dialog.Trigger asChild>
          <button type="button" className={`btn ${turningOff ? styles.stop : "btn-primary"}`} disabled={disabledReason !== null} title={disabledReason ?? undefined}>
            {turningOff ? "Stop factory…" : "Start factory…"}
          </button>
        </Dialog.Trigger>
        {disabledReason ? (
          <span className={`${styles.why} text-muted`} data-testid="kill-switch-reason">
            {disabledReason}
          </span>
        ) : null}
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-backdrop" />
          <Dialog.Content className={`dialog blueprint ${styles.dialog}`} aria-describedby={undefined}>
            <i className="corner tl" />
            <i className="corner tr" />
            <i className="corner bl" />
            <i className="corner br" />
            <Dialog.Title className="dialog-title">{turningOff ? "Stop the factory" : "Start the factory"}</Dialog.Title>
            {turningOff ? (
              <p className={styles.text}>
                Sets <span className="mono">FACTORY_ENABLED=0</span>: every live task is cancelled, its process tree gets SIGTERM within{" "}
                {KILL_SWITCH_BOUND_S} s, its container is removed, and its issue gets a comment. Nothing is admitted until the factory is
                turned back on.
              </p>
            ) : (
              <p className={styles.text}>
                Sets <span className="mono">FACTORY_ENABLED=1</span>. Nothing is re-admitted: a cancelled task returns only when its issue is
                labelled <span className="mono">factory:ready</span> again.
              </p>
            )}
            {phase.kind === "done" ? (
              <p className={styles.ok} role="status" data-testid="kill-switch-result">
                {phase.enabled ? "Factory on." : `Factory off · ${phase.live} live process${phase.live === 1 ? "" : "es"} left${phase.elapsed !== null ? ` · ${phase.elapsed} s` : ""}.`}{" "}
                Recorded as events row <span className="mono">#{phase.seq}</span> with your login <span className="mono">@{login}</span>.
              </p>
            ) : (
              <form
                className={styles.form}
                onSubmit={(event) => {
                  event.preventDefault();
                  void confirm();
                }}
              >
                <label className="field">
                  <span>Reason</span>
                  <input className="input" type="text" autoFocus required maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} disabled={phase.kind === "stopping"} />
                </label>
                <p className={`${styles.note} text-muted`}>
                  Recorded as an events row with your login <span className="mono">@{login ?? "?"}</span>.
                </p>
                {phase.kind === "stopping" ? (
                  <p className={styles.progress} role="status" aria-live="polite">
                    {turningOff ? countdownText(liveProcesses ?? 0, secondsLeft) : "Turning the factory on…"}
                  </p>
                ) : null}
                {phase.kind === "error" ? (
                  <p className={styles.error} role="alert">
                    {phase.message}
                  </p>
                ) : null}
                <div className="dialog-actions">
                  <Dialog.Close asChild>
                    <button type="button" className="btn btn-secondary">
                      Cancel
                    </button>
                  </Dialog.Close>
                  <button type="submit" className={`btn ${turningOff ? styles.confirmStop : "btn-primary"}`} disabled={!reason.trim() || phase.kind === "stopping"}>
                    {turningOff ? "Stop factory" : "Start factory"}
                  </button>
                </div>
              </form>
            )}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
