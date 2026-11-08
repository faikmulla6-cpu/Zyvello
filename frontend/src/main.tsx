import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { MotionConfig } from "motion/react";
import { DemoOne } from "@/demo";
import "@/styles/globals.css";

const AuthComponent = lazy(() => import("@/components/ui/sign-up").then(module => ({ default: module.AuthComponent })));

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <Suspense fallback={<p role="status" className="p-8 text-center">Loading Zyvello…</p>}>
        {window.location.pathname === "/signup" || window.location.pathname === "/login"
          ? <AuthComponent brandName="Zyvello" mode={window.location.pathname === "/signup" ? "signup" : "login"} />
          : <DemoOne />}
      </Suspense>
    </MotionConfig>
  </StrictMode>,
);
