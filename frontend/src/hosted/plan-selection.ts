const planCodes = ["starter", "growth", "business"] as const;
export type PlanCode = typeof planCodes[number];

export function selectedPlan(): PlanCode | undefined {
  const requested = new URLSearchParams(window.location.search).get("plan");
  return planCodes.find(code => code === requested);
}

export function planPage(path: "/signup" | "/login" | "/app", plan?: PlanCode): string {
  if (!plan) return path;
  return `${path}?plan=${plan}${path === "/app" ? "#account" : ""}`;
}
