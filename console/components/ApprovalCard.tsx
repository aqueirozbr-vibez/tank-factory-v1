"use client";

// "Approve plan" (ui-context.md · Component Library · ApprovalCard): writes the label on
// repo#issue as @login through the GitHub authorisation, bound to spec @ sha, holdout n tests
// all present; primary "Write factory:plan-approved", secondary "Open issue on GitHub ↗",
// footer "The GitHub label is the approval." Enter or Space activates; `a` focuses the button;
// loading "Writing label…"; an error names GitHub's answer and nothing is recorded; a disabled
// state names its reason; success reads "factory:plan-approved by @login · just now".

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import type { ApprovalState } from "@/lib/approvals";

import { isTypingTarget } from "./KeyboardMap";
import styles from "./ApprovalCard.module.css";

export const LABEL = "factory:plan-approved";

export type ApprovalCardProps = {
  taskId: string;
  issue: number;
  repo: string; // org/repo
  login: string | null;
  specSha: string | null;
  holdout: { present: number; total: number };
  state: ApprovalState;
  issueUrl: string;
  focusKey?: boolean; // the `a` key focuses this card's button (the first open plan)
};

type Phase = { kind: "idle" } | { kind: "writing" } | { kind: "done"; at: Date } | { kind: "error"; message: string };

export function ApprovalCard({ taskId, issue, repo, login, specSha, holdout, state, issueUrl, focusKey = false }: ApprovalCardProps) {
  const router = useRouter();
  const button = useRef<HTMLButtonElement | null>(null);
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });

  useEffect(() => {
    if (!focusKey) return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "a" || event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) return;
      event.preventDefault();
      button.current?.focus();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focusKey]);

  async function write() {
    setPhase({ kind: "writing" });
    try {
      const response = await fetch("/api/console/labels", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ task_id: taskId, issue, add: LABEL, spec_sha: specSha }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => ({}))) as { code?: string; message?: string };
        const text =
          response.status === 409
            ? "The spec sha changed since this page loaded; reload and read the spec again."
            : `GitHub refused the write (${response.status})${body.message ? `: ${body.message}` : ""}`;
        setPhase({ kind: "error", message: text });
        return;
      }
      setPhase({ kind: "done", at: new Date() });
      router.refresh();
    } catch {
      setPhase({ kind: "error", message: "The console could not reach GitHub; nothing was recorded." });
    }
  }

  const disabled = !state.allowed || phase.kind === "writing" || phase.kind === "done";
  const holdoutText = holdout.total === 0 ? "no holdout map" : holdout.present === holdout.total ? `holdout ${holdout.total} tests all present` : `holdout ${holdout.present} of ${holdout.total} present`;
  return (
    <section className={`card blueprint ${styles.card}`} aria-label={`Approve plan ${taskId}`}>
      <i className="corner tl" />
      <i className="corner tr" />
      <i className="corner bl" />
      <i className="corner br" />
      <p className="card-kicker mono">Approve plan</p>
      <p className={styles.line}>
        writes <span className="mono">{LABEL}</span> on <span className="mono">{repo}#{issue}</span> as{" "}
        <span className="mono">{login ? `@${login}` : "(not signed in)"}</span> through GitHub authorisation, bound to spec @{" "}
        <span className="mono">{specSha ? specSha.slice(0, 12) : "pending"}</span>, {holdoutText}
        {state.action === "governed_approve" ? (
          <>
            {" "}
            · governed path <span className="mono">{state.governedPath}</span> needs a CODEOWNER
          </>
        ) : null}
      </p>
      <div className={styles.actions}>
        <button
          ref={button}
          type="button"
          className="btn btn-primary"
          disabled={disabled}
          aria-disabled={disabled}
          onClick={() => void write()}
          title={state.reason ?? undefined}
        >
          {phase.kind === "writing" ? "Writing label…" : `Write ${LABEL}`}
        </button>
        <a href={issueUrl} target="_blank" rel="noreferrer" className="btn btn-secondary">
          Open issue on GitHub ↗
        </a>
      </div>
      {state.reason && phase.kind !== "done" ? (
        <p className={`${styles.reason} text-muted`} role="note">
          Disabled: {state.reason}
        </p>
      ) : null}
      {phase.kind === "error" ? (
        <p className={styles.error} role="alert">
          {phase.message} Nothing was recorded.
        </p>
      ) : null}
      {phase.kind === "done" ? (
        <p className={styles.success} role="status">
          <span className="mono">{LABEL}</span> by <span className="mono">@{login}</span> · just now · the approvals row binds to the spec sha
          when the orchestrator sees the label
        </p>
      ) : null}
      <p className={`${styles.footer} text-muted`}>The GitHub label is the approval.</p>
    </section>
  );
}
