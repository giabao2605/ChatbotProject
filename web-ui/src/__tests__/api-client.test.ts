import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiSend, apiUpload, setCsrfToken } from "@/api/client";

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api client CSRF handling", () => {
  beforeEach(() => {
    setCsrfToken("");
    vi.restoreAllMocks();
  });

  it("adds CSRF and JSON content type for mutating JSON requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("csrf-123");

    await apiSend("/api/settings/theme", "PUT", { value: "dark" });

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Headers;
    expect(init.credentials).toBe("include");
    expect(headers.get("X-CSRF-Token")).toBe("csrf-123");
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(init.body).toBe(JSON.stringify({ value: "dark" }));
  });

  it("adds CSRF without forcing content type for uploads", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);
    setCsrfToken("csrf-456");
    const form = new FormData();
    form.append("file", new File(["abc"], "doc.pdf"));

    await apiUpload("/api/documents/upload", form);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    const headers = init.headers as Headers;
    expect(headers.get("X-CSRF-Token")).toBe("csrf-456");
    expect(headers.has("Content-Type")).toBe(false);
    expect(init.body).toBe(form);
  });

  it("keeps a structured backend rejection readable for the UI", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: "Chưa hoàn tất wave trước; không thể kích hoạt Wave 2" }),
      { status: 422, headers: { "Content-Type": "application/json" } },
    )));

    await expect(apiSend("/api/catalog/departments/Sales/rollout-plan", "PUT", {}))
      .rejects.toThrow("Chưa hoàn tất wave trước; không thể kích hoạt Wave 2");
  });
});
