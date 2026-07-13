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

  it("reserves platform control-plane access for explicit platform admins", () => {
    expect(isRoleAllowed(["admin"], ["platform_admin"])).toBe(false);
    expect(isRoleAllowed(["platform_admin"], ["platform_admin"])).toBe(true);
    expect(visibleNavigationItems(["admin"]).map((item) => item.to)).not.toContain("/org");
    const platformRoutes = visibleNavigationItems(["platform_admin"]).map((item) => item.to);
    expect(platformRoutes).toContain("/org");
    expect(platformRoutes).not.toContain("/chat");
    expect(platformRoutes).not.toContain("/documents");
    expect(platformRoutes).not.toContain("/access");
  });
});
