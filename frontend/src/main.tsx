import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { MotionConfig } from "motion/react";
import { DemoOne } from "@/demo";
import "@/styles/globals.css";

const AuthComponent = lazy(() => import("@/components/ui/sign-up").then(module => ({ default: module.AuthComponent })));
const pagePath = window.location.pathname.replace(/\/+$/, "") || "/";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <Suspense fallback={<p role="status" className="p-8 text-center">Loading Zyvello…</p>}>
        {pagePath === "/signup" || pagePath === "/login"
          ? <AuthComponent brandName="Zyvello" mode={pagePath === "/signup" ? "signup" : "login"} />
          : <DemoOne />}
      </Suspense>
    </MotionConfig>
  </StrictMode>,
);
