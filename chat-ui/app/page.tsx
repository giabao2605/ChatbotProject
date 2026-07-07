"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

type Msg = {
  role: "user" | "assistant";
  content: string;
  refText?: string;
  image?: string | null;
  imageUrl?: string | null;
  imageName?: string | null;
  chatId?: number | null;
  feedback?: number | null;
};

type SessionItem = {
  session_id: string;
  thoi_gian?: string;
  cau_hoi: string;
  owner?: string;
};

type HistoryResponse = {
  messages?: Array<{
    role: "user" | "assistant";
    content: string;
    image?: string | null;
    chat_id?: number | null;
    danh_gia?: number | null;
    ref_images?: string[];
  }>;
};

type SessionsResponse = {
  sessions?: SessionItem[];
};

function createSessionId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function ChatPage() {
  const [ctx, setCtx] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState(createSessionId);
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [search, setSearch] = useState("");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [partIds, setPartIds] = useState<string[]>([]);
  const [convCtx, setConvCtx] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedPreview, setSelectedPreview] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setCtx(params.get("ctx"));
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  useEffect(() => {
    if (!ctx) return;
    refreshSessions(ctx).catch((e) => setWarning((e as Error).message));
  }, [ctx]);

  useEffect(() => {
    return () => {
      if (selectedPreview) URL.revokeObjectURL(selectedPreview);
    };
  }, [selectedPreview]);

  const filteredSessions = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) =>
      `${s.cau_hoi || ""} ${s.owner || ""}`.toLowerCase().includes(q),
    );
  }, [sessions, search]);

  async function refreshSessions(token = ctx) {
    if (!token) return;
    const resp = await fetch(`/api/sessions?ctx=${encodeURIComponent(token)}`, {
      cache: "no-store",
    });
    if (!resp.ok) throw new Error(await resp.text());
    const data = (await resp.json()) as SessionsResponse;
    setSessions(data.sessions ?? []);
  }

  function appendToLast(text: string) {
    setMessages((prev) => {
      const copy = [...prev];
      const last = copy[copy.length - 1];
      if (last && last.role === "assistant") {
        copy[copy.length - 1] = { ...last, content: last.content + text };
      }
      return copy;
    });
  }

  function patchLastAssistant(patch: Partial<Msg>) {
    setMessages((prev) => {
      const copy = [...prev];
      const last = copy[copy.length - 1];
      if (last && last.role === "assistant") {
        copy[copy.length - 1] = { ...last, ...patch };
      }
      return copy;
    });
  }

  function newChat() {
    if (busy) return;
    setSessionId(createSessionId());
    setMessages([]);
    setPartIds([]);
    setConvCtx(null);
    setError(null);
    setWarning(null);
    clearSelectedFile();
    taRef.current?.focus();
  }

  async function loadSession(nextSessionId: string) {
    if (!ctx || busy) return;
    setError(null);
    setWarning(null);
    const resp = await fetch("/api/history", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ctx, session_id: nextSessionId }),
    });
    if (!resp.ok) {
      setError(await resp.text());
      return;
    }
    const data = (await resp.json()) as HistoryResponse;
    setSessionId(nextSessionId);
    setMessages(
      (data.messages ?? []).map((m) => ({
        role: m.role,
        content: m.content || "",
        image: m.image ?? null,
        chatId: m.chat_id ?? null,
        feedback: m.danh_gia ?? null,
      })),
    );
    setPartIds([]);
    setConvCtx(null);
    clearSelectedFile();
  }

  async function deleteSession(targetSessionId: string) {
    if (!ctx || busy) return;
    const resp = await fetch("/api/sessions", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ctx, session_id: targetSessionId }),
    });
    if (!resp.ok) {
      setError(await resp.text());
      return;
    }
    if (targetSessionId === sessionId) newChat();
    await refreshSessions();
  }

  function clearSelectedFile() {
    if (selectedPreview) URL.revokeObjectURL(selectedPreview);
    setSelectedPreview(null);
    setSelectedFile(null);
    if (fileRef.current) fileRef.current.value = "";
  }

  function onFileChange(file: File | null) {
    clearSelectedFile();
    if (!file) return;
    setSelectedFile(file);
    setSelectedPreview(URL.createObjectURL(file));
  }

  async function uploadSelectedFile() {
    if (!ctx || !selectedFile) return { imagePath: null, imageName: null };
    const form = new FormData();
    form.append("ctx", ctx);
    form.append("file", selectedFile);
    const resp = await fetch("/api/upload", { method: "POST", body: form });
    if (!resp.ok) throw new Error(await resp.text());
    const data = (await resp.json()) as {
      image_path?: string;
      file_name?: string;
    };
    return {
      imagePath: data.image_path ?? null,
      imageName: data.file_name ?? selectedFile.name,
    };
  }

  async function send() {
    const question = input.trim();
    if (!question || busy) return;
    if (!ctx) {
      setError(
        "Thieu thong tin phien dang nhap. Hay mo chat tu tab Chatbot trong Streamlit.",
      );
      return;
    }
    setError(null);
    setWarning(null);
    setBusy(true);
    setInput("");

    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    let imagePath: string | null = null;
    let imageName: string | null = null;
    const imageUrl: string | null = selectedFile
      ? URL.createObjectURL(selectedFile)
      : null;

    try {
      const uploaded = await uploadSelectedFile();
      imagePath = uploaded.imagePath;
      imageName = uploaded.imageName;

      setMessages((prev) => [
        ...prev,
        {
          role: "user",
          content: question,
          image: imagePath,
          imageUrl,
          imageName,
        },
        { role: "assistant", content: "" },
      ]);
      setSelectedFile(null);
      setSelectedPreview(null);
      if (fileRef.current) fileRef.current.value = "";

      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ctx,
          session_id: sessionId,
          question,
          image_path: imagePath,
          chat_history: history,
          current_part_ids: partIds,
          conversation_context: convCtx,
        }),
      });
      if (!resp.ok || !resp.body) {
        const txt = await resp.text().catch(() => "");
        throw new Error(txt || `HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split("\n\n");
        buffer = chunks.pop() ?? "";
        for (const raw of chunks) {
          let ev = "message";
          let dataStr = "";
          for (const line of raw.split("\n")) {
            if (line.startsWith("event:")) ev = line.slice(6).trim();
            else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
          }
          if (!dataStr) continue;
          const data = JSON.parse(dataStr);
          if (ev === "delta") {
            appendToLast(data.text as string);
          } else if (ev === "warning") {
            setWarning(
              `${data.message || "Canh bao"}${data.detail ? `: ${data.detail}` : ""}`,
            );
          } else if (ev === "done") {
            setPartIds((data.new_part_ids as string[]) ?? []);
            setConvCtx(
              (data.conversation_context as Record<string, unknown>) ?? null,
            );
            patchLastAssistant({
              refText: (data.ref_text as string) || undefined,
              chatId: (data.chat_id as number | null) ?? null,
            });
            await refreshSessions();
          } else if (ev === "error") {
            throw new Error(
              (data.detail as string) ||
                (data.message as string) ||
                "Loi khong xac dinh",
            );
          }
        }
      }
    } catch (e) {
      const msg = (e as Error).message;
      setMessages((prev) => {
        const copy = [...prev];
        const last = copy[copy.length - 1];
        if (last && last.role === "assistant") {
          copy[copy.length - 1] = {
            ...last,
            content: last.content || `Loi: ${msg}`,
          };
        } else {
          copy.push({ role: "assistant", content: `Loi: ${msg}` });
        }
        return copy;
      });
    } finally {
      setBusy(false);
      taRef.current?.focus();
    }
  }

  async function sendFeedback(chatId: number, rating: number) {
    if (!ctx) return;
    const resp = await fetch("/api/feedback", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ctx, chat_id: chatId, rating }),
    });
    if (!resp.ok) {
      setWarning(await resp.text());
      return;
    }
    setMessages((prev) =>
      prev.map((m) => (m.chatId === chatId ? { ...m, feedback: rating } : m)),
    );
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  return (
    <div className="flex h-screen bg-[#0f0f12] text-gray-100">
      <aside className="hidden w-72 shrink-0 border-r border-white/10 bg-[#121218] md:flex md:flex-col">
        <div className="border-b border-white/10 p-3">
          <button
            onClick={newChat}
            className="w-full rounded-md bg-emerald-500 px-3 py-2 text-sm font-medium text-black transition hover:bg-emerald-400 disabled:opacity-40"
            disabled={busy}
          >
            Cuoc tro chuyen moi
          </button>
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Tim kiem lich su"
            className="mt-3 w-full rounded-md border border-white/10 bg-[#17171d] px-3 py-2 text-sm outline-none focus:border-emerald-400/60"
          />
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {filteredSessions.map((s) => (
            <div
              key={s.session_id}
              className={`mb-1 rounded-md border px-2 py-2 ${
                s.session_id === sessionId
                  ? "border-emerald-400/50 bg-emerald-400/10"
                  : "border-transparent hover:bg-white/5"
              }`}
            >
              <button
                onClick={() => loadSession(s.session_id)}
                className="block w-full text-left text-sm text-gray-100"
                disabled={busy}
                title={s.cau_hoi}
              >
                <span className="line-clamp-2">{s.cau_hoi}</span>
                {s.owner ? (
                  <span className="mt-1 block text-xs text-gray-500">
                    {s.owner}
                  </span>
                ) : null}
              </button>
              <button
                onClick={() => deleteSession(s.session_id)}
                className="mt-2 text-xs text-red-300 hover:text-red-200"
                disabled={busy}
              >
                Xoa
              </button>
            </div>
          ))}
          {filteredSessions.length === 0 ? (
            <p className="px-2 py-4 text-sm text-gray-500">
              Chua co lich su phu hop.
            </p>
          ) : null}
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-white/10 px-4 py-3">
          <div>
            <h1 className="text-sm font-semibold text-gray-100">
              Tro ly tai lieu noi bo
            </h1>
            <p className="text-xs text-gray-500">
              Chat Next.js, history SQL Server
            </p>
          </div>
          <button
            onClick={newChat}
            className="rounded-md border border-white/10 px-3 py-1.5 text-xs text-gray-300 transition hover:bg-white/5 md:hidden"
            disabled={busy}
          >
            Chat moi
          </button>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
            {messages.length === 0 && (
              <div className="mt-16 text-center text-gray-400">
                <p className="text-lg font-medium text-gray-200">Xin chao</p>
                <p className="mt-2 text-sm">
                  Dat cau hoi ve tai lieu, quy trinh, chinh sach hoac du lieu
                  noi bo.
                </p>
              </div>
            )}

            {messages.map((m, i) => (
              <div
                key={`${m.role}-${i}`}
                className={
                  m.role === "user" ? "flex justify-end" : "flex justify-start"
                }
              >
                <div
                  className={
                    m.role === "user"
                      ? "max-w-[85%] rounded-xl bg-[#2b2b36] px-4 py-3 text-gray-100"
                      : "max-w-[85%] rounded-xl bg-[#1e1e26] px-4 py-3 text-gray-100"
                  }
                >
                  {m.imageUrl ? (
                    <img
                      src={m.imageUrl}
                      alt={m.imageName || "Anh upload"}
                      className="mb-3 max-h-64 rounded-md border border-white/10 object-contain"
                    />
                  ) : m.imageName ? (
                    <div className="mb-2 text-xs text-gray-400">
                      File: {m.imageName}
                    </div>
                  ) : null}

                  {m.role === "assistant" && m.content === "" ? (
                    <TypingDots />
                  ) : m.role === "assistant" ? (
                    <div className="md-body text-[15px] leading-relaxed">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {m.content}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <div className="whitespace-pre-wrap text-[15px] leading-relaxed">
                      {m.content}
                    </div>
                  )}

                  {m.refText ? (
                    <details className="mt-3 rounded-md bg-black/20 p-2 text-xs text-gray-400">
                      <summary className="cursor-pointer select-none text-gray-300">
                        Nguon tham khao
                      </summary>
                      <div className="mt-2 whitespace-pre-wrap">{m.refText}</div>
                    </details>
                  ) : null}

                  {m.role === "assistant" && m.chatId ? (
                    <div className="mt-3 flex gap-2 text-xs">
                      <button
                        onClick={() => sendFeedback(m.chatId as number, 1)}
                        className={`rounded border px-2 py-1 ${
                          m.feedback === 1
                            ? "border-emerald-400 text-emerald-300"
                            : "border-white/10 text-gray-400 hover:text-gray-200"
                        }`}
                      >
                        Thich
                      </button>
                      <button
                        onClick={() => sendFeedback(m.chatId as number, -1)}
                        className={`rounded border px-2 py-1 ${
                          m.feedback === -1
                            ? "border-red-400 text-red-300"
                            : "border-white/10 text-gray-400 hover:text-gray-200"
                        }`}
                      >
                        Khong thich
                      </button>
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </div>

        {(error || warning) && (
          <div className="mx-auto w-full max-w-3xl px-4">
            {error ? (
              <div className="mb-2 rounded-md border border-red-500/30 bg-red-500/10 px-3 py-2 text-sm text-red-300">
                {error}
              </div>
            ) : null}
            {warning ? (
              <div className="mb-2 rounded-md border border-yellow-500/30 bg-yellow-500/10 px-3 py-2 text-sm text-yellow-200">
                {warning}
              </div>
            ) : null}
          </div>
        )}

        <footer className="border-t border-white/10 px-4 py-3">
          <div className="mx-auto max-w-3xl">
            {selectedFile ? (
              <div className="mb-2 flex items-center justify-between rounded-md border border-white/10 bg-[#17171d] px-3 py-2 text-sm text-gray-300">
                <span className="truncate">File: {selectedFile.name}</span>
                <button
                  onClick={clearSelectedFile}
                  className="ml-3 text-xs text-red-300 hover:text-red-200"
                  disabled={busy}
                >
                  Bo file
                </button>
              </div>
            ) : null}
            <div className="flex items-end gap-2">
              <label className="cursor-pointer rounded-xl border border-white/10 px-3 py-3 text-sm text-gray-300 transition hover:bg-white/5">
                Upload
                <input
                  ref={fileRef}
                  type="file"
                  accept=".png,.jpg,.jpeg,.bmp,.gif,.webp,.tif,.tiff"
                  className="hidden"
                  onChange={(e) => onFileChange(e.target.files?.[0] ?? null)}
                  disabled={busy}
                />
              </label>
              <textarea
                ref={taRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                rows={1}
                placeholder="Nhap cau hoi cua ban..."
                className="max-h-40 flex-1 resize-none rounded-xl border border-white/10 bg-[#17171d] px-4 py-3 text-[15px] text-gray-100 outline-none focus:border-emerald-400/60"
              />
              <button
                onClick={send}
                disabled={busy || !input.trim()}
                className="rounded-xl bg-emerald-500 px-4 py-3 text-sm font-medium text-black transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {busy ? "Dang tra loi..." : "Gui"}
              </button>
            </div>
            <p className="mt-2 text-center text-[11px] text-gray-500">
              Upload trong chat chi ho tro anh cau hoi. Tai lieu hoc moi van
              dung trang upload rieng.
            </p>
          </div>
        </footer>
      </main>
    </div>
  );
}

function TypingDots() {
  return (
    <div className="flex items-center gap-1 py-1">
      <span className="h-2 w-2 animate-bounce rounded-full bg-gray-400 [animation-delay:-0.3s]" />
      <span className="h-2 w-2 animate-bounce rounded-full bg-gray-400 [animation-delay:-0.15s]" />
      <span className="h-2 w-2 animate-bounce rounded-full bg-gray-400" />
    </div>
  );
}
