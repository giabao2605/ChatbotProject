import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory } from "vue-router";
import * as api from "@/api/client";
import { createAppRouter } from "@/router";
import { useAuthStore } from "@/stores/auth";
import type { UserProfile } from "@/types";

vi.mock("@/api/client", () => ({
  loadMe: vi.fn(),
  login: vi.fn(),
  refreshSession: vi.fn(),
  updatePreferredLanguage: vi.fn(),
  logout: vi.fn(),
}));

const user: UserProfile = {
  user_id: 1,
  username: "alice",
  display_name: "Alice",
  department: "CoKhi",
  roles: ["viewer"],
  allowed_departments: ["CoKhi"],
  max_security_level: "internal",
  allowed_sites: [],
  preferred_language: "en",
  csrf_token: "csrf",
};

describe("auth store", () => {
  beforeEach(() => {
    localStorage.clear();
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("loads the current user and syncs preferred language", async () => {
    vi.mocked(api.loadMe).mockResolvedValue(user);
    const auth = useAuthStore();

    await auth.loadMe();

    expect(auth.ready).toBe(true);
    expect(auth.user?.username).toBe("alice");
    expect(document.documentElement.getAttribute("lang")).toBe("en");
  });

  it("returns false when refresh fails", async () => {
    vi.mocked(api.refreshSession).mockRejectedValue(new Error("expired"));
    const auth = useAuthStore();

    await expect(auth.refresh()).resolves.toBe(false);
  });

  it.each(["loadMe", "refresh"] as const)("does not restore the session from late %s after local logout", async action => {
    vi.mocked(api.loadMe).mockResolvedValue(user);
    const auth = useAuthStore(); await auth.loadMe();
    let complete!: (value: UserProfile) => void;
    const pending = new Promise<UserProfile>(resolve => { complete = resolve; });
    if (action === "refresh") vi.mocked(api.refreshSession).mockReturnValue(pending);
    else vi.mocked(api.loadMe).mockReturnValue(pending);
    const request = auth[action]();
    vi.mocked(api.logout).mockResolvedValue(undefined);
    await auth.logout(); complete(user); await request;
    expect(auth.user).toBeNull();
  });

  it("keeps the session and emits no signal when server logout fails", async () => {
    vi.mocked(api.loadMe).mockResolvedValue(user);
    const auth = useAuthStore(); await auth.loadMe();
    vi.mocked(api.logout).mockRejectedValueOnce(new Error("offline"));
    await expect(auth.logout()).rejects.toThrow("offline");
    expect(auth.user?.username).toBe("alice");
    expect(localStorage.getItem("mech-chatbot.logout")).toBeNull();
  });

  it("still clears the local session when logout notification storage is unavailable", async () => {
    vi.mocked(api.loadMe).mockResolvedValue(user);
    const auth = useAuthStore(); await auth.loadMe();
    vi.mocked(api.logout).mockResolvedValue(undefined);
    const setItem = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage unavailable"); });
    try { await expect(auth.logout()).resolves.toBeUndefined(); expect(auth.user).toBeNull(); }
    finally { setItem.mockRestore(); }
  });

  it("does not clear a new login when an older loadMe fails after logout", async () => {
    const auth = useAuthStore();
    let fail!: (error: Error) => void;
    vi.mocked(api.loadMe).mockReturnValue(new Promise((_resolve, reject) => { fail = reject; }));
    const oldRequest = auth.loadMe();
    vi.mocked(api.logout).mockResolvedValue(undefined); await auth.logout();
    vi.mocked(api.login).mockResolvedValue({ ...user, username: "new-user" });
    await auth.login("new-user", "test-only");
    fail(new Error("old request expired")); await oldRequest;
    expect(auth.user?.username).toBe("new-user");
  });
});

describe("route guards", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
  });

  it("redirects anonymous users to login with next path", async () => {
    vi.mocked(api.loadMe).mockRejectedValue(new Error("401"));
    const router = createAppRouter(createMemoryHistory());

    await router.push("/documents");
    await router.isReady();

    expect(router.currentRoute.value.name).toBe("login");
    expect(router.currentRoute.value.query.next).toBe("/documents");
  });

  it("redirects authenticated users away from login", async () => {
    vi.mocked(api.loadMe).mockResolvedValue(user);
    const router = createAppRouter(createMemoryHistory());

    await router.push("/login");
    await router.isReady();

    expect(router.currentRoute.value.name).toBe("dashboard");
  });

  it("allows a viewer to open the role-aware dashboard", async () => {
    vi.mocked(api.loadMe).mockResolvedValue(user);
    const router = createAppRouter(createMemoryHistory());

    await router.push("/dashboard");
    await router.isReady();

    expect(router.currentRoute.value.name).toBe("dashboard");
  });

  it("allows only an explicit platform admin to open organization controls", async () => {
    vi.mocked(api.loadMe).mockResolvedValue({ ...user, roles: ["admin"] });
    const legacyAdminRouter = createAppRouter(createMemoryHistory());
    await legacyAdminRouter.push("/org");
    await legacyAdminRouter.isReady();
    expect(legacyAdminRouter.currentRoute.value.name).toBe("dashboard");

    setActivePinia(createPinia());
    vi.mocked(api.loadMe).mockResolvedValue({ ...user, roles: ["platform_admin"] });
    const platformAdminRouter = createAppRouter(createMemoryHistory());
    await platformAdminRouter.push("/org");
    await platformAdminRouter.isReady();
    expect(platformAdminRouter.currentRoute.value.name).toBe("org");
  });

  it("redirects legacy lifecycle links into the document library", async () => {
    vi.mocked(api.loadMe).mockResolvedValue({ ...user, roles: ["reviewer"] });
    const router = createAppRouter(createMemoryHistory());

    await router.push("/lifecycle");
    await router.isReady();

    expect(router.currentRoute.value.path).toBe("/documents");
    expect(router.currentRoute.value.query.tab).toBe("expired");
  });

  it.each([
    ["security_admin", "/access", "access"],
    ["platform_admin", "/analytics", "analytics"],
    ["reviewer", "/analytics", "analytics"],
    ["admin", "/analytics", "analytics"],
    ["viewer", "/analytics", "dashboard"],
    ["security_admin", "/analytics", "dashboard"],
    ["platform_admin", "/access", "dashboard"],
  ])("routes pure %s visiting %s to %s", async (role, path, expected) => {
    vi.mocked(api.loadMe).mockResolvedValue({ ...user, roles: [role] });
    const router = createAppRouter(createMemoryHistory());
    await router.push(path);
    await router.isReady();
    expect(router.currentRoute.value.name).toBe(expected);
  });

  it("shows a not-found route instead of an empty page for unknown URLs", async () => {
    vi.mocked(api.loadMe).mockResolvedValue(user);
    const router = createAppRouter(createMemoryHistory());

    await router.push("/QA_missing_route");
    await router.isReady();

    expect(router.currentRoute.value.name).toBe("not-found");
    expect(router.currentRoute.value.path).toBe("/QA_missing_route");
  });
});
