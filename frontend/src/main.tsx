import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { MotionConfig } from "motion/react";
import { DemoOne } from "@/demo";
import { SubscriptionPolicyPage } from "@/hosted/SubscriptionPolicyPage";
import "@/styles/globals.css";
import "@/hosted/gmail-return";

const LocalAuth = lazy(() => import("@/components/ui/sign-up").then(module => ({ default: module.AuthComponent })));
const HostedAuth = lazy(() => import("@/hosted/HostedAuthPage").then(module => ({ default: module.HostedAuthPage })));
const pagePath = window.location.pathname.replace(/\/+$/, "") || "/";
const hostedMode = import.meta.env.VITE_ZYVELLO_HOSTED_MODE === "true";
const isAuthPath = pagePath === "/signup" || pagePath === "/login";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <MotionConfig reducedMotion="user">
      <Suspense fallback={<p role="status" className="p-8 text-center">Loading Zyvello…</p>}>
        {pagePath === "/subscription-policy" ? <SubscriptionPolicyPage />
          : hostedMode && (isAuthPath || pagePath === "/app")
          ? <HostedAuth page={pagePath === "/signup" ? "signup" : pagePath === "/app" ? "app" : "login"} />
          : isAuthPath
            ? <LocalAuth brandName="Zyvello" mode={pagePath === "/signup" ? "signup" : "login"} />
            : <DemoOne />}
      </Suspense>
    </MotionConfig>
  </StrictMode>,
);
