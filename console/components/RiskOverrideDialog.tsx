"use client";

// Raise a task's risk class before pr_open (PRD Capability 2: a tech lead's override; TRD §6.1
// `POST /tasks/{id}/risk-override`): a centred, focus-trapped dialog with a required Reason;
// focus lands in Reason; `Esc` cancels; Enter confirms only when Reason is filled
// (ui-context.md · Layout Patterns · Dialogs).

import * as Dialog from "@radix-ui/react-dialog";
import { useRouter } from "next/navigation";
import { useState } from "react";

import styles from "./RiskOverrideDialog.module.css";

export type RiskOverrideDialogProps = { taskId: string; current: string | null };

const CHOICES = ["risk:high", "risk:governed"] as const;

export function RiskOverrideDialog({ taskId, current }: RiskOverrideDialogProps) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [risk, setRisk] = useState<(typeof CHOICES)[number]>(current === "risk:high" ? "risk:governed" : "risk:high");
  const [reason, setReason] = useState("");
  const [state, setState] = useState<{ kind: "idle" } | { kind: "sending" } | { kind: "error"; message: string }>({ kind: "idle" });

  async function confirm() {
    if (!reason.trim()) return;
    setState({ kind: "sending" });
    try {
      const response = await fetch(`/api/console/approvals/risk-override/${encodeURIComponent(taskId)}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ risk, reason: reason.trim() }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { message?: string };
        setState({ kind: "error", message: body.message ?? `the console answered ${response.status}` });
        return;
      }
      setState({ kind: "idle" });
      setOpen(false);
      router.refresh();
    } catch {
      setState({ kind: "error", message: "the orchestrator is unreachable" });
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <button type="button" className="btn btn-ghost">
          Raise risk…
        </button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-backdrop" />
        <Dialog.Content className={`dialog blueprint ${styles.dialog}`} aria-describedby={undefined}>
          <i className="corner tl" />
          <i className="corner tr" />
          <i className="corner bl" />
          <i className="corner br" />
          <Dialog.Title className="dialog-title">Raise the risk of {taskId}</Dialog.Title>
          <p className={styles.text}>
            The task is <span className="mono">{current ?? "unclassified"}</span>. Raising it adds a plan approval
            {risk === "risk:governed" ? " by a CODEOWNER" : ""} before the build; the score stays on record and the
            override is written as a control event with your login.
          </p>
          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault();
              void confirm();
            }}
          >
            <fieldset className={styles.choices}>
              <legend className={styles.legend}>New class</legend>
              {CHOICES.map((choice) => (
                <label key={choice} className={`${styles.option} mono`}>
                  <input type="radio" name="risk" value={choice} checked={risk === choice} disabled={choice === current} onChange={() => setRisk(choice)} />
                  {choice}
                </label>
              ))}
            </fieldset>
            <label className="field">
              <span>Reason</span>
              <input
                className="input"
                type="text"
                autoFocus
                required
                maxLength={500}
                value={reason}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
            {state.kind === "error" ? (
              <p className={styles.error} role="alert">
                {state.message}
              </p>
            ) : null}
            <div className="dialog-actions">
              <Dialog.Close asChild>
                <button type="button" className="btn btn-secondary">
                  Cancel
                </button>
              </Dialog.Close>
              <button type="submit" className="btn btn-primary" disabled={!reason.trim() || state.kind === "sending"}>
                {state.kind === "sending" ? "Recording…" : `Set ${risk}`}
              </button>
            </div>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
