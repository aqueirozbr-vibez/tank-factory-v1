"use client";

// The keyboard map (ui-context.md · Layout Patterns; TRD §5.2): 1 to 6 go to the screens, `j`
// and `k` move between rows (`[data-row]` elements the tables mark), Enter opens the focused
// row's first link or button, `?` shows the map. `a`, `p` and `Esc` belong to the components
// that own those elements.

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import styles from "./KeyboardMap.module.css";

export const SCREEN_KEYS: Record<string, string> = {
  "1": "/",
  "2": "/tasks",
  "3": "/tasks/review",
  "4": "/approvals",
  "5": "/eval",
  "6": "/config",
};

export const MAP: ReadonlyArray<readonly [string, string]> = [
  ["1 – 6", "go to Overview, Tasks, Review, Approvals, Eval, Configuration"],
  ["j / k", "move between rows"],
  ["Enter", "open the focused task, gate or approval"],
  ["a", "focus the approval button on an open plan"],
  ["p", "pause and resume the live tail"],
  ["Esc", "close a drawer or dialog"],
  ["?", "show this map"],
];

export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return (
    tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable === true
  );
}

/** The rows on the screen, in document order. */
export function rowsOnScreen(root: ParentNode = document): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("[data-row]"));
}

/** Focus the next or previous row from the one that holds focus (the first when none does). */
export function moveRow(direction: 1 | -1, root: ParentNode = document): HTMLElement | null {
  const rows = rowsOnScreen(root);
  if (rows.length === 0) return null;
  const active = document.activeElement;
  const index = rows.findIndex((row) => row === active || (active instanceof Node && row.contains(active)));
  const next = index < 0 ? (direction === 1 ? 0 : rows.length - 1) : Math.min(rows.length - 1, Math.max(0, index + direction));
  const row = rows[next] ?? null;
  if (row) {
    row.focus();
    if (typeof row.scrollIntoView === "function") row.scrollIntoView({ block: "nearest" }); // absent under jsdom
  }
  return row;
}

/** The row that holds focus, or null. */
export function focusedRow(): HTMLElement | null {
  const active = document.activeElement;
  return active instanceof HTMLElement ? active.closest<HTMLElement>("[data-row]") : null;
}

/** Enter opens a row: its first link or enabled button. */
export function openRow(row: HTMLElement): boolean {
  const target = row.querySelector<HTMLElement>("a[href], button:not([disabled])");
  if (!target) return false;
  target.click();
  return true;
}

export function KeyboardMap() {
  const router = useRouter();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) return;
      if (event.key === "?") {
        event.preventDefault();
        setOpen((value) => !value);
        return;
      }
      if (event.key === "Escape" && open) {
        setOpen(false);
        return;
      }
      if (event.key === "j" || event.key === "k") {
        if (moveRow(event.key === "j" ? 1 : -1)) event.preventDefault();
        return;
      }
      if (event.key === "Enter") {
        const row = focusedRow();
        if (row && document.activeElement === row && openRow(row)) event.preventDefault();
        return;
      }
      const href = SCREEN_KEYS[event.key];
      if (href) {
        event.preventDefault();
        router.push(href);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router, open]);

  if (!open) return null;
  return (
    <div className="dialog-backdrop" role="presentation" onClick={() => setOpen(false)}>
      <div
        className={`dialog blueprint ${styles.dialog}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby="keyboard-map-title"
        onClick={(event) => event.stopPropagation()}
      >
        <i className="corner tl" />
        <i className="corner tr" />
        <i className="corner bl" />
        <i className="corner br" />
        <h2 id="keyboard-map-title" className="dialog-title">
          Keyboard map
        </h2>
        <dl className={styles.list}>
          {MAP.map(([keys, what]) => (
            <div key={keys} className={styles.row}>
              <dt className="mono">{keys}</dt>
              <dd>{what}</dd>
            </div>
          ))}
        </dl>
        <div className="dialog-actions">
          <button type="button" className="btn btn-secondary" onClick={() => setOpen(false)}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
