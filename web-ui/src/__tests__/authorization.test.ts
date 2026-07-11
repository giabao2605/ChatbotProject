import { describe, expect, it } from "vitest";
import { isRoleAllowed, visibleNavigationItems } from "@/authorization";

describe("dashboard authorization policy", () => {
  it("does not expose the admin dashboard to a viewer", () => {
    const visibleRoutes = visibleNavigationItems(["viewer"]).map((item) => item.to);

    expect(visibleRoutes).not.toContain("/dashboard");
    expect(isRoleAllowed(["viewer"], ["admin"])).toBe(false);
  });

  it("keeps the dashboard visible to an admin", () => {
    const visibleRoutes = visibleNavigationItems(["admin"]).map((item) => item.to);

    expect(visibleRoutes).toContain("/dashboard");
    expect(isRoleAllowed(["admin"], ["admin"])).toBe(true);
  });
});
