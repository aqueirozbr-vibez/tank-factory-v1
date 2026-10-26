"use client";

// Under 1280 px every route lands on /unsupported (TRD §5.1; ui-context.md · Layout Patterns).
// No component implements a mobile branch; this is the only width rule.

import { usePathname, useRouter } from "next/navigation";
import { useEffect } from "react";

export const MIN_WIDTH = 1280;

export function ViewportGuard() {
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    function check() {
      if (window.innerWidth < MIN_WIDTH && pathname !== "/unsupported") {
        router.replace("/unsupported");
      }
    }
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, [router, pathname]);

  return null;
}
