"use client";

// Raise one task's budget once, by at most 2×, with a Reason (PRD Capability 5; TRD §6.1
// `POST /tasks/{id}/budget`): a centred dialog, focus in the amount then Reason, `Esc` cancels;
// the orchestrator keeps the once-and-2× rule and answers 409 or 422; the raise is the control
// event a budget block's resume looks for.

import * as Dialog from "@radix-ui/react-dialog";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { raiseBounds, raiseDisabledReason } from "@/lib/controls";

import styles from "./BudgetRaiseDialog.module.css";

export type BudgetRaiseDialogProps = {
  taskId: string;
  current: number;
  raised: boolean;
  status: string;
  login: string | null;
  permission: string | null;
  allowed: boolean;
};

type Phase = { kind: "idle" } | { kind: "sending" } | { kind: "done"; from: number; to: number; seq: number } | { kind: "error"; message: string };

export function BudgetRaiseDialog({ taskId, current, raised, status, login, permission, allowed }: BudgetRaiseDialogProps) {
  const router = useRouter();
  const bounds = raiseBounds(current);
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState(bounds.max.toFixed(2));
  const [reason, setReason] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const disabledReason = raiseDisabledReason({ allowed, permission, raised, status });
  const value = Number(amount);
  const valid = Number.isFinite(value) && value > current && value <= bounds.max + 1e-9;

  async function confirm() {
    if (!valid || !reason.trim()) return;
    setPhase({ kind: "sending" });
    try {
      const response = await fetch(`/api/console/tasks/${encodeURIComponent(taskId)}/budget`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ budget_usd: value, reason: reason.trim() }),
      });
      const body = (await response.json().catch(() => ({}))) as { old?: number; new?: number; event_seq?: number; message?: string };
      if (!response.ok) {
        setPhase({ kind: "error", message: body.message ?? `the console answered ${response.status}` });
        return;
      }
      setPhase({ kind: "done", from: body.old ?? current, to: body.new ?? value, seq: body.event_seq ?? 0 });
      router.refresh();
    } catch {
      setPhase({ kind: "error", message: "the orchestrator is unreachable" });
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <div className={styles.row}>
        <Dialog.Trigger asChild>
          <button type="button" className="btn btn-ghost" disabled={disabledReason !== null} title={disabledReason ?? undefined}>
            Raise budget…
          </button>
        </Dialog.Trigger>
        {disabledReason ? (
          <span className={`${styles.why} text-muted`} data-testid="raise-reason">
            {disabledReason}
          </span>
        ) : null}
      </div>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-backdrop" />
        <Dialog.Content className={`dialog blueprint ${styles.dialog}`} aria-describedby={undefined}>
          <i className="corner tl" />
          <i className="corner tr" />
          <i className="corner bl" />
          <i className="corner br" />
          <Dialog.Title className="dialog-title">Raise the budget of {taskId}</Dialog.Title>
          <p className={styles.text}>
            One raise per task, at most 2× the current <span className="mono">${current.toFixed(2)}</span> (up to{" "}
            <span className="mono">${bounds.max.toFixed(2)}</span>). Recorded as a control event with your login; a budget block resumes
            only after a raise.
          </p>
          {phase.kind === "done" ? (
            <p className={styles.ok} role="status">
              Budget raised ${phase.from.toFixed(2)} → ${phase.to.toFixed(2)} · events row <span className="mono">#{phase.seq}</span> with{" "}
              <span className="mono">@{login}</span>.
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
                <span>New budget (USD)</span>
                <input className="input" type="number" step="0.01" min={bounds.min} max={bounds.max} value={amount} autoFocus onChange={(event) => setAmount(event.target.value)} />
              </label>
              <label className="field">
                <span>Reason</span>
                <input className="input" type="text" required maxLength={500} value={reason} onChange={(event) => setReason(event.target.value)} />
              </label>
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
                <button type="submit" className="btn btn-primary" disabled={!valid || !reason.trim() || phase.kind === "sending"}>
                  {phase.kind === "sending" ? "Recording…" : "Raise budget"}
                </button>
              </div>
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
