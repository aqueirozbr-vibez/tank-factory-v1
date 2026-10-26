"use client";

// Theme follows prefers-color-scheme; a best-effort override lives in localStorage and is
// written to data-theme on <html> (ui-context.md · Theme; code-standards · Next.js).

import { useEffect } from "react";

export const THEME_KEY = "factory-console.theme";
export type ThemeOverride = "light" | "dark";

export function readThemeOverride(): ThemeOverride | null {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    return null;
  }
}

export function writeThemeOverride(value: ThemeOverride | null): void {
  try {
    if (value === null) window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, value);
  } catch {
    // best effort: a private window or blocked storage changes nothing
  }
}

export function applyTheme(override: ThemeOverride | null): void {
  const root = document.documentElement;
  if (override === null) root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", override);
}

export function ThemeRoot() {
  useEffect(() => {
    applyTheme(readThemeOverride());
  }, []);
  return null;
}
