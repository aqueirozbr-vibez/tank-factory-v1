// The chrome every screen shares (ui-context.md · Layout Patterns · Shell; TRD §5.2).
// Server component: installation name, the six-screen nav with the Approvals badge (a client
// island that polls every 30 s), the FACTORY_ENABLED pill from /healthz, the login pill from the
// session, the screen title and the Freshness stamp.

import Link from "next/link";
import type { ReactNode } from "react";

import { ApiError, health, read } from "@/lib/api";
import { auth } from "@/lib/auth";

import { Freshness } from "./Freshness";
import { NavBadge, type Counts } from "./NavBadge";
import styles from "./Shell.module.css";

export const SCREENS = [
  { key: "1", href: "/", label: "Overview" },
  { key: "2", href: "/tasks", label: "Tasks" },
  { key: "3", href: "/tasks/review", label: "Review" },
  { key: "4", href: "/approvals", label: "Approvals" },
  { key: "5", href: "/eval", label: "Eval" },
  { key: "6", href: "/config", label: "Configuration" },
] as const;

export type ShellProps = {
  title: string;
  children: ReactNode;
  current: (typeof SCREENS)[number]["href"];
};

async function installationName(): Promise<string> {
  try {
    const installation = await read("/api/v1/installation", { caller: null });
    return `${installation.org}/${installation.repo}`;
  } catch (error) {
    if (error instanceof ApiError) return "installation unavailable";
    return "orchestrator unreachable";
  }
}

async function needsHuman(): Promise<Counts | null> {
  try {
    return await read("/api/v1/needs-human/count", { caller: null });
  } catch {
    return null;
  }
}

async function viewer(): Promise<{ login: string; permission: string } | null> {
  try {
    const session = await auth();
    if (!session?.login) return null;
    return { login: session.login, permission: session.permission ?? "none" };
  } catch {
    return null;
  }
}

export async function Shell({ title, children, current }: ShellProps) {
  const [name, status, counts, user] = await Promise.all([
    installationName(),
    health(),
    needsHuman(),
    viewer(),
  ]);
  const enabled = status?.factory_enabled ?? null;
  return (
    <div className={styles.frame}>
      <header className={`nav ${styles.header}`}>
        <span className={`nav-brand ${styles.brand}`}>
          <span className="mono">{name}</span>
        </span>
        <nav aria-label="Screens" className={styles.nav}>
          {SCREENS.map((screen) => (
            <Link
              key={screen.href}
              href={screen.href}
              className={styles.navLink}
              aria-current={screen.href === current ? "page" : undefined}
            >
              <kbd className={styles.key}>{screen.key}</kbd>
              {screen.label}
              {screen.href === "/approvals" ? <NavBadge initial={counts} /> : null}
            </Link>
          ))}
        </nav>
        <span
          className={`${styles.pill} mono`}
          data-state={enabled === null ? "unknown" : enabled ? "on" : "off"}
          aria-label={
            enabled === null
              ? "Factory state unknown"
              : enabled
                ? "Factory enabled"
                : "Factory disabled"
          }
        >
          {enabled === null ? "FACTORY_ENABLED=?" : `FACTORY_ENABLED=${enabled ? 1 : 0}`}
        </span>
        {user ? (
          <span className={`${styles.pill} ${styles.login} mono`} title={`permission: ${user.permission}`}>
            @{user.login} · {user.permission}
          </span>
        ) : (
          <Link href="/auth/signin" className={`${styles.pill} ${styles.login} mono`}>
            sign in
          </Link>
        )}
      </header>
      <main className={styles.main}>
        <div className={styles.titleRow}>
          <h1 className={styles.title}>{title}</h1>
          <Freshness at={new Date()} />
        </div>
        {children}
      </main>
    </div>
  );
}
