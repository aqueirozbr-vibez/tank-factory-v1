"use client";

// Copies a JSON value to the clipboard and says so (ui-context.md · Component Library ·
// EnvelopeViewer footer "Copy JSON"). The clipboard call is best effort: a refusal reads
// "Copy failed" and nothing else changes.

import { useEffect, useState } from "react";

export type CopyJsonProps = { value: unknown; label?: string };

export function CopyJson({ value, label = "Copy JSON" }: CopyJsonProps) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  useEffect(() => {
    if (state === "idle") return;
    const timer = window.setTimeout(() => setState("idle"), 2000);
    return () => window.clearTimeout(timer);
  }, [state]);

  async function copy() {
    const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
  }

  return (
    <button type="button" className="btn btn-ghost" onClick={() => void copy()} aria-live="polite">
      {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : label}
    </button>
  );
}
