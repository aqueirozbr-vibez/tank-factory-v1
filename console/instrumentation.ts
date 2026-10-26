// Next.js instrumentation: the console process subscribes to the orchestrator's `/changes`
// stream once, in the Node runtime, so tagged configuration and eval reads revalidate when
// a configuration loads or an eval run completes (TRD §5.2 cache keying).
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { startChangesListener } = await import("./lib/changes");
    startChangesListener();
  }
}
