import type { ChatContext } from "@/lib/bridge";

export function ragBaseUrl() {
  return (process.env.RAG_SERVER_URL || "http://127.0.0.1:8100").replace(
    /\/+$/,
    "",
  );
}

export function ragHeaders() {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const serviceToken = process.env.RAG_SERVICE_TOKEN || "";
  if (serviceToken) headers["X-RAG-Service-Token"] = serviceToken;
  return headers;
}

export function userPayload(ctx: ChatContext) {
  return {
    user_id: ctx.user_id ?? null,
    username: ctx.username ?? null,
  };
}

export async function ragPost<T>(
  path: string,
  payload: Record<string, unknown>,
): Promise<T> {
  const resp = await fetch(`${ragBaseUrl()}${path}`, {
    method: "POST",
    headers: ragHeaders(),
    body: JSON.stringify(payload),
    cache: "no-store",
  });
  if (!resp.ok) {
    const detail = await resp.text().catch(() => "");
    throw new Error(detail || `RAG server error HTTP ${resp.status}`);
  }
  return (await resp.json()) as T;
}
