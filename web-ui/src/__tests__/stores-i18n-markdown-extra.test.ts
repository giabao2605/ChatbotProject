import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/api/client";
import {
  currentLocale,
  i18nState,
  initLocale,
  setLocale,
  t,
  useI18n,
} from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useChatStore } from "@/stores/chat";
import { renderMarkdown } from "@/utils/markdown";
import type { UserProfile } from "@/types";

vi.mock("@/api/client", () => ({
  loadMe: vi.fn(),
  login: vi.fn(),
  loginErrorMessage: (error: unknown) => error instanceof Error ? error.message : "Đăng nhập thất bại",
  refreshSession: vi.fn(),
  updatePreferredLanguage: vi.fn(),
  logout: vi.fn(),
}));

const user: UserProfile = {
  user_id: 9,
  username: "bao",
  display_name: "Bảo",
  department: "ME",
  roles: ["admin"],
  allowed_departments: ["ME"],
  max_security_level: "internal",
  allowed_sites: ["HCM"],
  preferred_language: "vi",
  csrf_token: "csrf",
};

describe("auth and chat store branches", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("logs in, refreshes, updates language, and logs out", async () => {
    vi.mocked(api.login).mockResolvedValue(user);
    vi.mocked(api.refreshSession).mockResolvedValue({
      ...user,
      preferred_language: "en",
    });
    vi.mocked(api.updatePreferredLanguage).mockRejectedValue(
      new Error("preference offline"),
    );
    const auth = useAuthStore();

    await auth.login("bao", "password");
    expect(auth.loading).toBe(false);
    expect(auth.ready).toBe(true);
    expect(auth.isAdmin).toBe(true);
    await expect(auth.refresh()).resolves.toBe(true);
    expect(currentLocale()).toBe("en");
    await expect(auth.setLanguage("vi")).resolves.toBeUndefined();
    expect(auth.user?.preferred_language).toBe("vi");
    await auth.logout();
    expect(api.logout).toHaveBeenCalledOnce();
    expect(auth.user).toBeNull();
  });

  it("surfaces Error and non-Error login failures", async () => {
    const auth = useAuthStore();
    vi.mocked(api.login).mockRejectedValueOnce(new Error("bad password"));
    await expect(auth.login("bao", "bad")).rejects.toThrow("bad password");
    expect(auth.error).toBe("bad password");
    expect(auth.loading).toBe(false);

    vi.mocked(api.login).mockRejectedValueOnce("offline");
    await expect(auth.login("bao", "bad")).rejects.toBe("offline");
    expect(auth.error).toBe("Đăng nhập thất bại");
  });

  it("falls back to a local session id and clears explicit/default memory", () => {
    vi.stubGlobal("crypto", {});
    vi.spyOn(Date, "now").mockReturnValue(1234);
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    setActivePinia(createPinia());
    const chat = useChatStore();
    expect(chat.sessionId).toBe("1234-8");

    chat.openSession("a");
    chat.updateMemory("a", {
      currentPartIds: ["P1"],
      conversationContext: { topic: "pump" },
    });
    chat.openSession("b");
    chat.updateMemory("b", {
      currentPartIds: ["P2"],
      conversationContext: null,
    });
    chat.clearMemory("a");
    chat.clearMemory();
    expect(chat.memoryBySession.a.currentPartIds).toEqual([]);
    expect(chat.currentMemory.currentPartIds).toEqual([]);
    chat.newSession();
    expect(chat.sessionId).toBe("1234-8");
  });
});

describe("i18n failure-safe behavior", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    setLocale("vi");
  });

  it("honors preferred, stored, and default locale order", () => {
    localStorage.setItem("mech_locale", "en");
    initLocale("vi");
    expect(currentLocale()).toBe("vi");
    initLocale("unsupported");
    expect(currentLocale()).toBe("vi");
    localStorage.setItem("mech_locale", "en");
    initLocale();
    expect(currentLocale()).toBe("en");
  });

  it("survives unavailable storage and exposes the lightweight adapter", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    initLocale();
    expect(currentLocale()).toBe("vi");
    expect(document.documentElement.lang).toBe("vi");
    const adapter = useI18n();
    expect(adapter.state).toBe(i18nState);
    expect(adapter.t("missing.key")).toBe("missing.key");
  });

  it("replaces every named placeholder occurrence", () => {
    setLocale("vi");
    expect(t("upload.success", { id: 42 })).toContain("42");
  });
});

describe("extended markdown rendering", () => {
  it("renders paragraphs, unordered and ordered lists, and ragged tables", () => {
    const html = renderMarkdown([
      "# Main",
      "A **bold** line",
      "second *line*",
      "",
      "- one",
      "* two",
      "",
      "1. first",
      "2. second",
      "",
      "| A | B |",
      "|:---|---:|",
      "| one |",
    ].join("\n"));

    expect(html).toContain("<h3>Main</h3>");
    expect(html).toContain("<br>");
    expect(html).toContain("<ul><li>one</li><li>two</li></ul>");
    expect(html).toContain("<ol><li>first</li><li>second</li></ol>");
    expect(html).toContain("<td></td>");
  });

  it("handles empty input and escapes all inline HTML-sensitive characters", () => {
    expect(renderMarkdown("")).toBe("");
    expect(renderMarkdown(`& < > " ' \`code\``)).toContain(
      "&amp; &lt; &gt; &quot; &#39; <code>code</code>",
    );
  });
});
