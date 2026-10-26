// The one orchestrator client of the console (TRD §5.2, §6.1; code-standards · Next.js).
// Server-side only: the service token never reaches the browser. Live reads use
// `cache: 'no-store'`; configuration and eval reads carry tags (`config`, `config:<sha>`,
// `eval`, `eval:<run_id>`) that the `/changes` listener (lib/changes.ts) revalidates. Every
// error is the orchestrator's `{code, message, detail?, traceId}`. Writes go through
// `postJson` with the actor and an Idempotency-Key.

import type { paths } from "./api.types";

export type ApiPaths = paths;
type GetPaths = {
  [P in keyof paths]: paths[P] extends { get: unknown } ? P : never;
}[keyof paths];
type Ok<P extends GetPaths> = paths[P] extends {
  get: { responses: { 200: { content: { "application/json": infer T } } } };
}
  ? T
  : never;

export type ErrorBody = {
  code: string;
  message: string;
  detail?: unknown;
  traceId: string;
};

export class ApiError extends Error {
  readonly status: number;
  readonly body: ErrorBody;

  constructor(status: number, body: ErrorBody) {
    super(`${body.code}: ${body.message}`);
    this.status = status;
    this.body = body;
  }
}

export type Caller = { actor: string };

const LIVE = new Set<string>(["/api/v1/tasks", "/api/v1/kpis", "/api/v1/indicators", "/api/v1/needs-human/count"]);

export function baseUrl(): string {
  const url = process.env.ORCHESTRATOR_URL;
  if (!url) throw new Error("ORCHESTRATOR_URL is not set");
  return url.replace(/\/$/, "");
}

export function serviceHeaders(caller: Caller | null): Record<string, string> {
  const headers: Record<string, string> = { accept: "application/json" };
  const token = process.env.FACTORY_SERVICE_TOKEN;
  if (token) headers["X-Factory-Service-Token"] = token;
  if (caller) headers["X-Factory-Actor"] = caller.actor;
  return headers;
}

export type FetchOptions = {
  caller: Caller | null;
  tags?: string[];
  revalidate?: number;
  query?: Record<string, string | number | undefined>;
};

export function withQuery(path: string, query: FetchOptions["query"]): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined) params.set(key, String(value));
  }
  const encoded = params.toString();
  return encoded ? `${path}?${encoded}` : path;
}

/** The task id goes into the path encoded as one segment (`acme%2Fatlas-api%23418`). */
export function taskPath(taskId: string, suffix = ""): string {
  return `/api/v1/tasks/${encodeURIComponent(taskId)}${suffix}`;
}

async function parseError(response: Response): Promise<ApiError> {
  let body: ErrorBody = {
    code: "http_" + response.status,
    message: response.statusText || "the orchestrator answered with an error",
    traceId: "",
  };
  try {
    const parsed: unknown = await response.json();
    if (parsed && typeof parsed === "object" && "code" in parsed && "message" in parsed) {
      body = parsed as ErrorBody;
    }
  } catch {
    // keep the synthetic body
  }
  return new ApiError(response.status, body);
}

/** The fetch options a read takes: live reads skip the cache; tagged reads sit in it until revalidated. */
export function cacheInit(path: string, options: Pick<FetchOptions, "tags" | "revalidate">): RequestInit & { next?: { tags?: string[]; revalidate?: number } } {
  const init: RequestInit & { next?: { tags?: string[]; revalidate?: number } } = {};
  const tagged = options.tags !== undefined || options.revalidate !== undefined;
  if (!tagged || LIVE.has(path)) {
    init.cache = "no-store";
    return init;
  }
  init.next = {};
  if (options.tags) init.next.tags = options.tags;
  if (options.revalidate !== undefined) init.next.revalidate = options.revalidate;
  else init.cache = "force-cache";
  return init;
}

export async function getJson<T>(path: string, options: FetchOptions): Promise<T> {
  const url = baseUrl() + withQuery(path, options.query);
  const init = cacheInit(path, options);
  init.headers = serviceHeaders(options.caller);
  const response = await fetch(url, init);
  if (!response.ok) throw await parseError(response);
  return (await response.json()) as T;
}

/** Typed reads for the documented GET routes; the type comes from the generated OpenAPI types. */
export function read<P extends GetPaths>(path: P, options: FetchOptions): Promise<Ok<P>> {
  return getJson<Ok<P>>(path, options);
}

export type PostOptions = {
  caller: Caller | null;
  idempotencyKey?: string;
};

/** A control write (TRD §6.1 write rows): the actor header and an Idempotency-Key; no caching. */
export async function postJson<T>(path: string, body: unknown, options: PostOptions): Promise<T> {
  const headers = serviceHeaders(options.caller);
  headers["content-type"] = "application/json";
  if (options.idempotencyKey) headers["Idempotency-Key"] = options.idempotencyKey;
  const response = await fetch(baseUrl() + path, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
    cache: "no-store",
  });
  if (!response.ok) throw await parseError(response);
  return (await response.json()) as T;
}

export type Health = {
  status: string;
  factory_enabled: boolean;
  db_ok: boolean;
  proxy_ok: boolean | null;
  last_poll_at: string | null;
  live_processes?: number | null;
  version: string;
};

export async function health(): Promise<Health | null> {
  try {
    const response = await fetch(baseUrl() + "/healthz", { cache: "no-store" });
    return (await response.json()) as Health;
  } catch {
    return null;
  }
}
