import type { Citation, ChatMessage, SessionItem, UserProfile } from "@/types";
import { parseSseBuffer } from "@/api/sse";

let csrfToken = "";

export function setCsrfToken(token: string) {
  csrfToken = token;
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (csrfToken && (init.method || "GET").toUpperCase() !== "GET") {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(path, {
    ...init,
    headers,
    credentials: "include",
    cache: "no-store",
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function login(username: string, password: string) {
  const data = await apiFetch<{ user: UserProfile }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  setCsrfToken(data.user.csrf_token);
  return data.user;
}

export async function loadMe() {
  const data = await apiFetch<{ user: UserProfile }>("/api/auth/me");
  setCsrfToken(data.user.csrf_token);
  return data.user;
}

export async function logout() {
  await apiFetch("/api/auth/logout", { method: "POST" });
  setCsrfToken("");
}

export async function listSessions() {
  const data = await apiFetch<{ sessions: SessionItem[] }>("/api/chat/sessions");
  return data.sessions ?? [];
}

export async function loadHistory(sessionId: string) {
  const data = await apiFetch<{ messages: ChatMessage[] }>("/api/chat/history", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId }),
  });
  return data.messages ?? [];
}

export async function deleteSession(sessionId: string) {
  await apiFetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
  });
}

export async function uploadChatImage(file: File) {
  const form = new FormData();
  form.append("file", file);
  return apiFetch<{ image_id: string; image_token: string; file_name: string }>(
    "/api/chat/upload-image",
    {
      method: "POST",
      body: form,
    },
  );
}

export type ChatStreamCallbacks = {
  onThinking: () => void;
  onDelta: (text: string) => void;
  onWarning: (message: string) => void;
  onDone: (data: {
    chat_id?: number | null;
    ref_text?: string;
    citations?: Citation[];
    new_part_ids?: string[];
    conversation_context?: Record<string, unknown> | null;
  }) => void;
  onError: (message: string) => void;
};

export async function sendChatMessage(
  payload: Record<string, unknown>,
  callbacks: ChatStreamCallbacks,
) {
  const response = await fetch("/api/chat/message", {
    method: "POST",
    credentials: "include",
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok || !response.body) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parsed = parseSseBuffer(buffer);
    buffer = parsed.rest;
    for (const item of parsed.events) {
      const data = item.data as Record<string, unknown>;
      if (item.event === "thinking") callbacks.onThinking();
      if (item.event === "delta") callbacks.onDelta(String(data.text ?? ""));
      if (item.event === "warning") callbacks.onWarning(String(data.message ?? ""));
      if (item.event === "done") callbacks.onDone(data);
      if (item.event === "error") callbacks.onError(String(data.detail || data.message || "Unknown error"));
    }
  }
}

export async function sendFeedback(chatId: number, rating: number) {
  await apiFetch("/api/chat/feedback", {
    method: "POST",
    body: JSON.stringify({ chat_id: chatId, rating }),
  });
}

export async function loadDashboard() {
  return apiFetch<{
    stats: Record<string, number>;
    recent_documents: unknown[];
    recent_failed_jobs: unknown[];
  }>("/api/dashboard");
}
