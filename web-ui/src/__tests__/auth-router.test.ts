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

    expect(router.currentRoute.value.name).toBe("chat");
  });
});
