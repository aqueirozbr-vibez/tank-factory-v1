export const dynamic = "force-static";

export default function UnsupportedPage() {
  return (
    <main style={{ padding: "var(--space-8) 16px", maxWidth: 640 }}>
      <h1
        style={{
          fontFamily: "var(--font-heading)",
          fontWeight: 600,
          fontSize: 28,
          lineHeight: 1.15,
          letterSpacing: "-0.015em",
        }}
      >
        Factory Console needs a window at least 1280 px wide
      </h1>
      <p>Widen the window, or open the console from a desktop browser.</p>
    </main>
  );
}
