import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  apiGet,
  apiSend,
  deleteSession,
  getCsrfToken,
  listSessions,
  loadDashboard,
  loadHistory,
  loadMe,
  login,
  logout,
  refreshSession,
  sendChatMessage,
  sendFeedback,
  setCsrfToken,
  updatePreferredLanguage,
  uploadChatImage,
  type ChatStreamCallbacks,
} from "@/api/client";
import type { UserProfile } from "@/types";

const sampleAccessValue = ["test", "only", "value"].join("-");

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function profile(csrfToken: string): UserProfile {
  return {
    user_id: 7,
    username: "bao",
    roles: ["Admin"],
    allowed_departments: ["Engineering"],
    max_security_level: "Internal",
    allowed_sites: ["HCM"],
    csrf_token: csrfToken,
  };
}

function request(fetchMock: ReturnType<typeof vi.fn>, index = 0) {
  return fetchMock.mock.calls[index] as [string, RequestInit];
}

describe("extended API client behavior", () => {
  beforeEach(() => {
    setCsrfToken("");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("serializes meaningful query values and skips absent filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ rows: [1] }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      apiGet("/api/jobs", {
        search: "bơm ly tâm",
        page: 0,
        active: false,
        empty: "",
        missing: undefined,
        nil: null,
      }),
    ).resolves.toEqual({ rows: [1] });

    expect(request(fetchMock)[0]).toBe(
      "/api/jobs?search=b%C6%A1m+ly+t%C3%A2m&page=0&active=false",
    );
    expect(request(fetchMock)[1]).toMatchObject({
      credentials: "include",
      cache: "no-store",
    });
  });

  it("leaves paths unchanged when no usable query is provided", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse({ ok: true })));
    vi.stubGlobal("fetch", fetchMock);

    await apiGet("/api/jobs");
    await apiGet("/api/jobs", { search: "", cursor: null });

    expect(request(fetchMock, 0)[0]).toBe("/api/jobs");
    expect(request(fetchMock, 1)[0]).toBe("/api/jobs");
  });

  it.each([
    ["empty response", new Response(null, { status: 204 }), undefined],
    ["empty success body", new Response("", { status: 200 }), undefined],
  ])("returns undefined for %s", async (_case, response, expected) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(apiSend("/api/jobs/7", "DELETE")).resolves.toBe(expected);
  });

  it.each([
    [
      "validation list",
      JSON.stringify({
        detail: ["Tên không hợp lệ", { msg: "Thiếu phòng ban" }, null, { code: "ignored" }],
      }),
      "Tên không hợp lệ; Thiếu phòng ban",
    ],
    [
      "empty validation list",
      JSON.stringify({ detail: [null, { code: "ignored" }], message: "Fallback" }),
      "Fallback",
    ],
    ["message object", JSON.stringify({ message: "Dịch vụ đang bận" }), "Dịch vụ đang bận"],
    ["plain text", "upstream unavailable", "upstream unavailable"],
    ["empty body", "", "HTTP 503"],
    [
      "unrecognized JSON",
      JSON.stringify({ detail: "  ", message: "  " }),
      JSON.stringify({ detail: "  ", message: "  " }),
    ],
    ["non-string message", JSON.stringify({ message: 42 }), JSON.stringify({ message: 42 })],
  ])("surfaces a readable %s error", async (_case, body, expected) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(body, {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(apiGet("/api/failing")).rejects.toThrow(expected);
  });

  it("falls back to the status when an error body cannot be read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        text: vi.fn().mockRejectedValue(new Error("socket closed")),
      }),
    );

    await expect(apiGet("/api/failing")).rejects.toThrow("HTTP 502");
  });

  it("updates CSRF state through login, current-user load, and refresh", async () => {
    const loginUser = profile("login-token");
    const currentUser = profile("me-token");
    const refreshedUser = profile("refresh-token");
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ user: loginUser }))
      .mockResolvedValueOnce(jsonResponse({ user: currentUser }))
      .mockResolvedValueOnce(jsonResponse({ user: refreshedUser }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(login("bao", sampleAccessValue)).resolves.toEqual(loginUser);
    expect(getCsrfToken()).toBe("login-token");
    await expect(loadMe()).resolves.toEqual(currentUser);
    expect(getCsrfToken()).toBe("me-token");
    await expect(refreshSession()).resolves.toEqual(refreshedUser);
    expect(getCsrfToken()).toBe("refresh-token");

    expect(request(fetchMock, 0)).toEqual([
      "/api/auth/login",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ username: "bao", password: sampleAccessValue }),
      }),
    ]);
    expect(request(fetchMock, 1)[0]).toBe("/api/auth/me");
    expect(request(fetchMock, 2)[0]).toBe("/api/auth/refresh");
  });

  it("clears CSRF state after logout and sends the current token first", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("logout-token");

    await logout();

    expect((request(fetchMock)[1].headers as Headers).get("X-CSRF-Token")).toBe("logout-token");
    expect(getCsrfToken()).toBe("");
  });

  it("updates the preferred language with JSON and CSRF headers", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("preferences-token");

    await updatePreferredLanguage("en");

    const [, init] = request(fetchMock);
    const headers = init.headers as Headers;
    expect(init).toMatchObject({
      method: "PATCH",
      body: JSON.stringify({ language: "en" }),
    });
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-CSRF-Token")).toBe("preferences-token");
  });

  it("lists sessions and loads history while tolerating omitted arrays", async () => {
    const sessions = [{ session_id: "s-1", cau_hoi: "Bơm nào?" }];
    const messages = [{ role: "assistant" as const, content: "Bơm P-101" }];
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ sessions }))
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(jsonResponse({ messages }))
      .mockResolvedValueOnce(jsonResponse({}));
    vi.stubGlobal("fetch", fetchMock);

    await expect(listSessions()).resolves.toEqual(sessions);
    await expect(listSessions()).resolves.toEqual([]);
    await expect(loadHistory("s-1")).resolves.toEqual(messages);
    await expect(loadHistory("s-2")).resolves.toEqual([]);

    expect(request(fetchMock, 2)).toEqual([
      "/api/chat/history",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ session_id: "s-1" }),
      }),
    ]);
  });

  it("encodes session identifiers before deletion", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await deleteSession("shift/A 01");

    expect(request(fetchMock)[0]).toBe("/api/chat/sessions/shift%2FA%2001");
    expect(request(fetchMock)[1].method).toBe("DELETE");
  });

  it("uploads a chat image as multipart form data", async () => {
    const upload = { image_id: "img-7", image_token: "token-7", file_name: "pump.png" };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(upload));
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("upload-token");
    const file = new File(["image"], "pump.png", { type: "image/png" });

    await expect(uploadChatImage(file)).resolves.toEqual(upload);

    const [, init] = request(fetchMock);
    const headers = init.headers as Headers;
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBe(file);
    expect(headers.has("Content-Type")).toBe(false);
    expect(headers.get("X-CSRF-Token")).toBe("upload-token");
  });

  it("dispatches every supported SSE event across fragmented chunks", async () => {
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('event: thinking\ndata: {}\n\nevent: del'));
        controller.enqueue(
          encoder.encode(
            'ta\ndata: {"text":"Xin "}\n\nevent: token\ndata: {"text":"chào"}\n\n' +
              "event: delta\ndata: {}\n\n" +
              'event: warning\ndata: {"message":"Nguồn yếu"}\n\n' +
              "event: warning\ndata: {}\n\n",
          ),
        );
        controller.enqueue(
          encoder.encode(
            'event: done\ndata: {"chat_id":12,"new_part_ids":["P-101"]}\n\n' +
              'event: error\ndata: {"detail":"Lỗi chi tiết"}\n\n' +
              'event: error\ndata: {"message":"Lỗi dự phòng"}\n\n' +
              "event: error\ndata: {}\n\n",
          ),
        );
        controller.close();
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(new Response(stream, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("stream-token");
    const callbacks = {
      onThinking: vi.fn(),
      onDelta: vi.fn(),
      onWarning: vi.fn(),
      onDone: vi.fn(),
      onError: vi.fn(),
    } satisfies ChatStreamCallbacks;

    await sendChatMessage({ question: "P-101?" }, callbacks);

    expect(request(fetchMock)).toEqual([
      "/api/chat/message",
      {
        method: "POST",
        credentials: "include",
        cache: "no-store",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": "stream-token",
        },
        body: JSON.stringify({ question: "P-101?" }),
      },
    ]);
    expect(callbacks.onThinking).toHaveBeenCalledOnce();
    expect(callbacks.onDelta.mock.calls).toEqual([["Xin "], ["chào"], [""]]);
    expect(callbacks.onWarning.mock.calls).toEqual([["Nguồn yếu"], [""]]);
    expect(callbacks.onDone).toHaveBeenCalledWith({ chat_id: 12, new_part_ids: ["P-101"] });
    expect(callbacks.onError.mock.calls).toEqual([
      ["Lỗi chi tiết"],
      ["Lỗi dự phòng"],
      ["Unknown error"],
    ]);
  });

  it.each([
    [
      "backend rejection",
      new Response("Không được phép", { status: 403 }),
      "Không được phép",
    ],
    ["missing stream", new Response(null, { status: 200 }), "HTTP 200"],
  ])("rejects a chat request with %s", async (_case, response, expected) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
    const callbacks = {
      onThinking: vi.fn(),
      onDelta: vi.fn(),
      onWarning: vi.fn(),
      onDone: vi.fn(),
      onError: vi.fn(),
    } satisfies ChatStreamCallbacks;

    await expect(sendChatMessage({}, callbacks)).rejects.toThrow(expected);
  });

  it("uses the status when a rejected chat response body cannot be read", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        body: null,
        text: vi.fn().mockRejectedValue(new Error("socket closed")),
      }),
    );
    const callbacks = {
      onThinking: vi.fn(),
      onDelta: vi.fn(),
      onWarning: vi.fn(),
      onDone: vi.fn(),
      onError: vi.fn(),
    } satisfies ChatStreamCallbacks;

    await expect(sendChatMessage({}, callbacks)).rejects.toThrow("HTTP 502");
  });

  it("sends feedback and returns dashboard data", async () => {
    const dashboard = {
      stats: { documents: 2 },
      recent_documents: [{ id: 1 }],
      recent_failed_jobs: [],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse(dashboard));
    vi.stubGlobal("fetch", fetchMock);

    await sendFeedback(12, 1);
    await expect(loadDashboard()).resolves.toEqual(dashboard);

    expect(request(fetchMock, 0)).toEqual([
      "/api/chat/feedback",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ chat_id: 12, rating: 1 }),
      }),
    ]);
    expect(request(fetchMock, 1)[0]).toBe("/api/dashboard");
  });
});
