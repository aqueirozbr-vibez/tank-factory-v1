"use client";

// Propose a `.factory/` change (PRD Capability 6: standing configuration is never applied
// directly; the console opens a PR under the user's login, gated on the eval set; TRD §6.1 BFF
// `POST /api/console/config-pr`): choose the file, give the exact text to find at main and its
// replacement, and a Reason; the dialog shows the file as it is at main. `Esc` cancels.

import * as Dialog from "@radix-ui/react-dialog";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { FACTORY_FILES, type FactoryFile } from "@/lib/config";

import styles from "./ProposeChangeDialog.module.css";

export type ProposeChangeDialogProps = {
  files: Array<{ file: FactoryFile; text: string | null }>;
  allowed: boolean;
  reason: string | null;
  initialFile?: FactoryFile;
};

type Phase = { kind: "idle" } | { kind: "sending" } | { kind: "opened"; url: string; branch: string } | { kind: "error"; message: string };

export function ProposeChangeDialog({ files, allowed, reason, initialFile }: ProposeChangeDialogProps) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<FactoryFile>(initialFile ?? FACTORY_FILES[0]);
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  const [why, setWhy] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const current = files.find((f) => f.file === file)?.text ?? null;
  const ready = allowed && find.trim().length > 0 && why.trim().length > 0 && find !== replace && phase.kind !== "sending";

  async function submit() {
    if (!ready) return;
    setPhase({ kind: "sending" });
    try {
      const response = await fetch("/api/console/config-pr", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ file, edits: [{ find, replace }], reason: why.trim() }),
      });
      const body = (await response.json().catch(() => ({}))) as { pr_url?: string; branch?: string; message?: string };
      if (!response.ok) {
        setPhase({ kind: "error", message: body.message ?? `the console answered ${response.status}` });
        return;
      }
      setPhase({ kind: "opened", url: body.pr_url ?? "#", branch: body.branch ?? "" });
      router.refresh();
    } catch {
      setPhase({ kind: "error", message: "GitHub is unreachable; nothing was opened" });
    }
  }

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setPhase({ kind: "idle" });
      }}
    >
      <Dialog.Trigger asChild>
        <button type="button" className="btn btn-primary" disabled={!allowed} title={reason ?? undefined}>
          Propose change…
        </button>
      </Dialog.Trigger>
      {!allowed && reason ? <span className={`${styles.why} text-muted`}>{reason}</span> : null}
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-backdrop" />
        <Dialog.Content className={`dialog blueprint ${styles.dialog}`} aria-describedby={undefined}>
          <i className="corner tl" />
          <i className="corner tr" />
          <i className="corner bl" />
          <i className="corner br" />
          <Dialog.Title className="dialog-title">Propose a .factory/ change</Dialog.Title>
          <p className={styles.text}>
            Opens a pull request on <span className="mono">factory-config/&lt;login&gt;/&lt;ts&gt;</span> under your GitHub login. Nothing is applied
            before merge: the eval set runs on the PR and a drop over 10 points blocks it.
          </p>
          {phase.kind === "opened" ? (
            <p className={styles.ok} role="status">
              Opened{" "}
              <a href={phase.url} target="_blank" rel="noreferrer">
                the pull request ↗
              </a>{" "}
              on <span className="mono">{phase.branch}</span>. It shows under Open changes with its eval result.
            </p>
          ) : (
            <form
              className={styles.form}
              onSubmit={(event) => {
                event.preventDefault();
                void submit();
              }}
            >
              <label className="field">
                <span>File</span>
                <select className="input" value={file} onChange={(event) => setFile(event.target.value as FactoryFile)}>
                  {FACTORY_FILES.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </label>
              <details className={styles.current}>
                <summary className={styles.summary}>The file at main</summary>
                <pre className={`${styles.pre} mono`} tabIndex={0}>{current ?? "(not readable without a GitHub session; the edit is checked against main when it is opened)"}</pre>
              </details>
              <label className="field">
                <span>Find (exact text, once in the file)</span>
                <textarea className={`input ${styles.area} mono`} rows={3} value={find} onChange={(event) => setFind(event.target.value)} autoFocus />
              </label>
              <label className="field">
                <span>Replace with</span>
                <textarea className={`input ${styles.area} mono`} rows={3} value={replace} onChange={(event) => setReplace(event.target.value)} />
              </label>
              <label className="field">
                <span>Reason</span>
                <input className="input" type="text" required maxLength={500} value={why} onChange={(event) => setWhy(event.target.value)} />
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
                <button type="submit" className="btn btn-primary" disabled={!ready}>
                  {phase.kind === "sending" ? "Opening PR…" : "Open pull request"}
                </button>
              </div>
            </form>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
