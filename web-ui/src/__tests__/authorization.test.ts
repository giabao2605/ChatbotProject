import { describe, expect, it } from "vitest";
import { isRoleAllowed, visibleNavigationItems } from "@/authorization";

describe("role-aware navigation policy", () => {
  it("exposes the role-aware dashboard but not management navigation to a viewer", () => {
    const visibleRoutes = visibleNavigationItems(["viewer"]).map((item) => item.to);

    expect(visibleRoutes).toContain("/dashboard");
    expect(visibleRoutes).not.toContain("/dictionary");
    expect(isRoleAllowed(["viewer"], ["admin"])).toBe(false);
  });

  it("keeps the dashboard visible to an admin", () => {
    const visibleRoutes = visibleNavigationItems(["admin"]).map((item) => item.to);

    expect(visibleRoutes).toContain("/dashboard");
    expect(visibleRoutes).toContain("/dictionary");
    expect(isRoleAllowed(["admin"], ["admin"])).toBe(true);
  });
});
