import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { defineComponent, h, nextTick } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "@/App.vue";
import * as api from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import type { UserProfile } from "@/types";

vi.mock("@/api/client", async importOriginal => ({
  ...await importOriginal<typeof import("@/api/client")>(),
  loadMe: vi.fn(), login: vi.fn(), refreshSession: vi.fn(),
  logout: vi.fn(), updatePreferredLanguage: vi.fn(),
}));

const user: UserProfile = {
  user_id: 1, username: "alice", display_name: "Alice", department: "QA",
  roles: ["viewer"], allowed_departments: ["QA"], max_security_level: "internal",
  allowed_sites: [], preferred_language: "en", csrf_token: "test-csrf",
};
const logoutKey = "mech-chatbot.logout";
let wrapper: VueWrapper | undefined;

function peerLogoutSignal() {
  window.dispatchEvent(new StorageEvent("storage", {
    key: logoutKey, newValue: "peer-logout", storageArea: localStorage,
  }));
}

async function mountProtectedApp() {
  const pinia = createPinia(); setActivePinia(pinia);
  const auth = useAuthStore(pinia); await auth.loadMe();
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: "/private", component: defineComponent({ render: () => h("p", { "data-private": "" }, "QA private source") }) },
    { path: "/login", meta: { public: true }, component: defineComponent({ render: () => h("p", "Login page") }) },
  ] });
  await router.push("/private"); await router.isReady();
  wrapper = mount(App, { global: { plugins: [pinia, router], stubs: { Button: true } } });
  return { auth, router };
}

describe("App cross-tab logout", () => {
  beforeEach(() => {
    localStorage.clear(); vi.resetAllMocks();
    vi.mocked(api.loadMe).mockResolvedValue(user);
    vi.mocked(api.logout).mockResolvedValue(undefined);
  });
  afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

  it("removes mounted private content when a peer successfully logs out", async () => {
    const { auth, router } = await mountProtectedApp();
    expect(wrapper!.find("[data-private]").exists()).toBe(true);
    const sender = useAuthStore(createPinia()); await sender.loadMe(); await sender.logout();
    const nonce = localStorage.getItem(logoutKey);
    expect(nonce).toBeTruthy();
    window.dispatchEvent(new StorageEvent("storage", { key: logoutKey, newValue: nonce, storageArea: localStorage }));
    await nextTick();
    expect(wrapper!.find("[data-private]").exists()).toBe(false);
    await flushPromises();
    expect(auth.user).toBeNull();
    expect(wrapper!.find(".account-name").exists()).toBe(false);
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it.each(["loadMe", "refresh", "login"] as const)("does not restore private content from late %s after peer logout", async action => {
    const { auth, router } = await mountProtectedApp();
    let complete!: (value: UserProfile) => void;
    const pending = new Promise<UserProfile>(resolve => { complete = resolve; });
    if (action === "refresh") vi.mocked(api.refreshSession).mockReturnValue(pending);
    else vi.mocked(api[action]).mockReturnValue(pending);
    const request = action === "login" ? auth.login("alice", "test-only") : auth[action]();
    peerLogoutSignal(); await flushPromises();
    complete(user); await request; await flushPromises();
    expect(auth.user).toBeNull();
    expect(wrapper!.find("[data-private]").exists()).toBe(false);
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("removes the storage listener when App unmounts", async () => {
    const { auth } = await mountProtectedApp();
    wrapper!.unmount(); wrapper = undefined;
    peerLogoutSignal(); await flushPromises();
    expect(auth.user?.username).toBe("alice");
  });

  it("removes mounted private content and account navigation after a real API 401", async () => {
    const { auth, router } = await mountProtectedApp(); api.setCsrfToken("old-csrf");
    expect(wrapper!.find("[data-private]").exists()).toBe(true);
    expect(wrapper!.find(".sidebar").exists()).toBe(true);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"detail":"Session expired"}', { status: 401 })));
    await expect(api.apiGet("/api/documents")).rejects.toThrow("Session expired"); await flushPromises();
    expect(api.getCsrfToken()).toBe(""); expect(auth.user).toBeNull();
    expect(wrapper!.find("[data-private]").exists()).toBe(false);
    expect(wrapper!.find(".account-name").exists()).toBe(false);
    expect(wrapper!.find(".sidebar").exists()).toBe(false);
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("does not restore an expired mounted session from a late successful loadMe", async () => {
    const { auth, router } = await mountProtectedApp(); api.setCsrfToken("old-csrf");
    const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
    vi.mocked(api.loadMe).mockImplementation(actual.loadMe);
    let complete!: (response: Response) => void;
    const oldResponse = new Promise<Response>(resolve => { complete = resolve; });
    vi.stubGlobal("fetch", vi.fn().mockReturnValueOnce(oldResponse).mockResolvedValueOnce(new Response('{"detail":"Session expired"}', { status: 401 })));
    const oldRequest = auth.loadMe();
    await expect(api.apiGet("/api/documents")).rejects.toThrow("Session expired"); await flushPromises();
    complete(new Response(JSON.stringify({ user }))); await oldRequest; await flushPromises();
    expect(api.getCsrfToken()).toBe("");
    expect(auth.user).toBeNull(); expect(wrapper!.find("[data-private]").exists()).toBe(false);
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("removes the unauthorized listener when App unmounts", async () => {
    const { auth } = await mountProtectedApp(); api.setCsrfToken("old-csrf");
    wrapper!.unmount(); wrapper = undefined;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response('{"detail":"Session expired"}', { status: 401 })));
    await expect(api.apiGet("/api/documents")).rejects.toThrow("Session expired"); await flushPromises();
    expect(auth.user?.username).toBe("alice");
  });
});
