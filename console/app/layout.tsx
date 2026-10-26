import type { Metadata } from "next";
import type { ReactNode } from "react";

import { KeyboardMap } from "@/components/KeyboardMap";
import { ThemeRoot } from "@/components/ThemeRoot";
import { ViewportGuard } from "@/components/ViewportGuard";

import "@/styles/industry.css";
import "@/styles/console-tokens.css";

export const metadata: Metadata = {
  title: "Factory Console",
  description: "The operations console of Tank Factory v1",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;700&family=Barlow+Condensed:wght@400;600&family=JetBrains+Mono:wght@400;500&display=swap"
        />
      </head>
      <body>
        <ThemeRoot />
        <ViewportGuard />
        <KeyboardMap />
        {children}
      </body>
    </html>
  );
}
