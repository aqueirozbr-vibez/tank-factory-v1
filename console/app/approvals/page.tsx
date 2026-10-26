import Link from "next/link";

import { AuditTable } from "@/components/AuditTable";
import { PlanQueue } from "@/components/PlanQueue";
import { QuestionsTable } from "@/components/QuestionsTable";
import { Shell } from "@/components/Shell";
import { ApiError, read } from "@/lib/api";
import {
  TABS,
  codeownerOf,
  codeownerRules,
  loadApprovals,
  parseMonth,
  parseTab,
  shiftMonth,
  type Tab,
  type Viewer,
} from "@/lib/approvals";
import { loadSession, userToken } from "@/lib/auth";
import { allowed } from "@/lib/authz";
import type { Repo } from "@/lib/github";

import styles from "./page.module.css";

export const dynamic = "force-dynamic";

const TAB_LABELS: Record<Tab, string> = { plans: "Plans", questions: "Questions", audit: "Audit" };

async function repository(caller: { actor: string } | null, errors: string[]): Promise<Repo | null> {
  try {
    const installation = await read("/api/v1/installation", { caller });
    return { owner: installation.org, repo: installation.repo };
  } catch (error) {
    errors.push(error instanceof ApiError ? `installation: ${error.body.message}` : "installation: unreachable");
    return null;
  }
}

// Approvals (TRD §5.2 `/approvals?tab=plans|questions|audit`; ui-context.md · Layout Patterns ·
// Approvals): the plan queue with SpecView and ApprovalCard, the questions table, the audit
// strata and table. Tabs are URL state; the viewer's permission and CODEOWNER status decide
// what each control may do, and the orchestrator and GitHub decide again when it acts.
export default async function ApprovalsPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string | string[]; month?: string | string[] }>;
}) {
  const params = await searchParams;
  const tab = parseTab(params.tab);
  const month = parseMonth(params.month);
  const session = await loadSession();
  const viewer: Viewer = session ? { login: session.login, permission: session.permission } : null;
  const caller = viewer ? { actor: viewer.login } : null;
  const errors: string[] = [];
  const [data, repo] = await Promise.all([loadApprovals(caller, month), repository(caller, errors)]);
  errors.push(...data.errors);
  const repoUrl = repo ? `https://github.com/${repo.owner}/${repo.repo}` : null;

  let codeowner = new Map<string, boolean>();
  const governedPaths = data.plans
    .filter((p) => p.risk === "risk:governed")
    .map((p) => p.governed_path ?? p.spec_path)
    .filter((p): p is string => Boolean(p));
  if (viewer && repo && governedPaths.length > 0) {
    try {
      const token = await userToken();
      if (token) codeowner = codeownerOf(viewer.login, governedPaths, await codeownerRules(token, repo));
    } catch {
      errors.push("CODEOWNERS: GitHub did not answer; governed approvals stay disabled");
    }
  }
  const canOverride = viewer !== null && allowed(viewer.permission, "risk_override");
  const canDecide = viewer !== null && allowed(viewer.permission, "audit_decide");
  const decideReason = viewer ? (canDecide ? null : `no write access (${viewer.permission})`) : "sign in to decide";
  const auditMonth = data.month ?? month;
  const counts: Record<Tab, number> = { plans: data.plans.length, questions: data.questions.length, audit: data.audit.length };

  return (
    <Shell title="Approvals" current="/approvals">
      <nav aria-label="Approval queues" className={styles.tabs}>
        {TABS.map((t) => (
          <Link key={t} href={`/approvals?tab=${t}`} className={styles.tab} aria-current={t === tab ? "page" : undefined}>
            {TAB_LABELS[t]} <span className="mono text-muted">{counts[t]}</span>
          </Link>
        ))}
      </nav>
      {errors.length > 0 ? (
        <p className={styles.errors} role="status">
          Some reads failed: {errors.join("; ")}
        </p>
      ) : null}
      {tab === "plans" ? (
        <PlanQueue items={data.plans} viewer={viewer} repo={repo} codeowner={codeowner} canOverride={canOverride} />
      ) : null}
      {tab === "questions" ? <QuestionsTable items={data.questions} repoUrl={repoUrl} /> : null}
      {tab === "audit" ? (
        <>
          {auditMonth ? (
            <nav aria-label="Audit month" className={styles.months}>
              <Link href={`/approvals?tab=audit&month=${shiftMonth(auditMonth, -1)}`} className="btn btn-ghost">
                ‹ {shiftMonth(auditMonth, -1)}
              </Link>
              <span className="mono">{auditMonth}</span>
              <Link href={`/approvals?tab=audit&month=${shiftMonth(auditMonth, 1)}`} className="btn btn-ghost">
                {shiftMonth(auditMonth, 1)} ›
              </Link>
            </nav>
          ) : null}
          <AuditTable items={data.audit} month={auditMonth} repoUrl={repoUrl} canDecide={canDecide} reason={decideReason} />
        </>
      ) : null}
    </Shell>
  );
}
