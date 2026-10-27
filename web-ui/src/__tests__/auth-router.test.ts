import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory } from "vue-router";
import { flushPromises } from "@vue/test-utils";
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

  it("shares session restoration while navigation is pending", async () => {
    let finish!: (value: UserProfile) => void;
    vi.mocked(api.loadMe).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const auth = useAuthStore();
    const first = auth.loadMe();
    const second = auth.loadMe();

    expect(api.loadMe).toHaveBeenCalledTimes(1);
    finish(user);
    await Promise.all([first, second]);
    expect(auth.user?.username).toBe("alice");
    expect(auth.ready).toBe(true);
  });

  it("does not restore a user after logout finishes", async () => {
    let finish!: (value: UserProfile) => void;
    vi.mocked(api.loadMe).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    vi.mocked(api.logout).mockResolvedValue(undefined);
    const auth = useAuthStore();
    const restore = auth.loadMe();
    const signal = vi.mocked(api.loadMe).mock.calls[0][0];

    await auth.logout();
    expect(signal?.aborted).toBe(true);
    finish(user);
    await restore;

    expect(auth.user).toBeNull();
    expect(auth.ready).toBe(true);
  });

  it("does not start a new restoration while logout is pending", async () => {
    const restores: Array<(value: UserProfile) => void> = [];
    let finishLogout!: () => void;
    vi.mocked(api.loadMe).mockImplementation(() => new Promise(resolve => { restores.push(resolve); }));
    vi.mocked(api.logout).mockImplementation(() => new Promise(resolve => { finishLogout = resolve; }));
    const auth = useAuthStore();
    const initialRestore = auth.loadMe();
    const logout = auth.logout();
    const router = createAppRouter(createMemoryHistory());
    const navigation = router.push("/documents");
    await flushPromises();
    finishLogout();
    await logout;
    restores.forEach(resolve => resolve(user));
    await Promise.all([initialRestore, navigation]);
    expect(api.loadMe).toHaveBeenCalledTimes(1);
    expect(auth.user).toBeNull();
    expect(router.currentRoute.value.name).toBe("login");
  });

  it("does not replace a newly logged in user with an old restore response", async () => {
    let finish!: (value: UserProfile) => void;
    vi.mocked(api.loadMe).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    vi.mocked(api.login).mockResolvedValue({ ...user, username: "bob" });
    const auth = useAuthStore();
    const restore = auth.loadMe();

    await auth.login("bob", "password");
    finish(user);
    await restore;

    expect(auth.user?.username).toBe("bob");
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

  it("keeps the latest destination when auth restoration delays navigation", async () => {
    let finish!: (value: UserProfile) => void;
    vi.mocked(api.loadMe).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    const router = createAppRouter(createMemoryHistory());
    const first = router.push("/dashboard");
    await vi.waitFor(() => expect(api.loadMe).toHaveBeenCalled());
    const latest = router.push("/documents?tab=expired");
    await new Promise(resolve => setTimeout(resolve, 0));

    expect(api.loadMe).toHaveBeenCalledTimes(1);
    finish(user);
    await Promise.all([first, latest]);

    expect(router.currentRoute.value.fullPath).toBe("/documents?tab=expired");
  });

  it("preserves the latest destination when a delayed session has expired", async () => {
    let finish!: (reason: Error) => void;
    vi.mocked(api.loadMe).mockImplementation(() => new Promise((_resolve, reject) => { finish = reject; }));
    const router = createAppRouter(createMemoryHistory());
    const first = router.push("/dashboard");
    await vi.waitFor(() => expect(api.loadMe).toHaveBeenCalled());
    const latest = router.push("/documents?tab=expired");
    await new Promise(resolve => setTimeout(resolve, 0));
    finish(new Error("401"));
    await Promise.all([first, latest]);

    expect(api.loadMe).toHaveBeenCalledTimes(1);
    expect(router.currentRoute.value.name).toBe("login");
    expect(router.currentRoute.value.query.next).toBe("/documents?tab=expired");
  });
});
