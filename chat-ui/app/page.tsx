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
    <div className="flex h-screen overflow-hidden bg-transparent text-gray-100 font-sans selection:bg-emerald-500/30">
      <aside className="hidden h-screen w-72 shrink-0 overflow-hidden border-r border-white/5 bg-neutral-900/40 backdrop-blur-xl md:flex md:flex-col shadow-[4px_0_24px_rgba(0,0,0,0.2)] z-10 relative">
        <div className="border-b border-white/5 p-4">
          <button
            onClick={newChat}
            className="w-full rounded-xl bg-gradient-to-r from-emerald-500 to-teal-500 px-4 py-2.5 text-sm font-semibold text-neutral-950 transition-all hover:from-emerald-400 hover:to-teal-400 disabled:opacity-40 shadow-[0_0_15px_rgba(16,185,129,0.3)] hover:shadow-[0_0_20px_rgba(16,185,129,0.5)] flex items-center justify-center gap-2"
            disabled={busy}
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
            Cuoc tro chuyen moi
          </button>
          <div className="relative mt-4">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Tim kiem lich su..."
              className="w-full rounded-xl border border-white/10 bg-white/5 pl-9 pr-3 py-2 text-sm outline-none transition-all hover:border-white/20 focus:border-emerald-500/50 focus:bg-white/10 shadow-inner placeholder:text-gray-500"
            />
          </div>
        </div>
        <div className="flex-1 overflow-y-auto p-3 space-y-1">
          {filteredSessions.map((s) => (
            <div
              key={s.session_id}
              className={`group rounded-xl border px-3 py-2.5 transition-all duration-200 ${
                s.session_id === sessionId
                  ? "border-emerald-500/30 bg-emerald-500/10 shadow-[inset_0_0_10px_rgba(16,185,129,0.05)]"
                  : "border-transparent hover:bg-white/5 hover:border-white/5"
              }`}
            >
              <button
                onClick={() => loadSession(s.session_id)}
                className="block w-full text-left text-sm text-gray-200 group-hover:text-white transition-colors"
                disabled={busy}
                title={s.cau_hoi}
              >
                <span className="line-clamp-2 leading-relaxed">{s.cau_hoi}</span>
                {s.owner ? (
                  <span className="mt-1.5 flex items-center gap-1.5 text-xs text-gray-500 font-medium">
                    <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
                    {s.owner}
                  </span>
                ) : null}
              </button>
              <button
                onClick={() => deleteSession(s.session_id)}
                className="mt-2.5 flex items-center gap-1 text-[11px] font-medium text-gray-500 opacity-0 transition-all hover:text-red-400 group-hover:opacity-100"
                disabled={busy}
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
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

      <main className="relative flex h-screen min-w-0 flex-1 flex-col overflow-hidden">
        <header className="z-20 flex shrink-0 items-center justify-between border-b border-white/5 bg-neutral-950/40 px-6 py-4 backdrop-blur-md">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-500/20 to-teal-500/20 border border-emerald-500/30">
              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-emerald-400"><path d="M12 8V4H8"></path><rect width="16" height="12" x="4" y="8" rx="2"></rect><path d="M2 14h2"></path><path d="M20 14h2"></path><path d="M15 13v2"></path><path d="M9 13v2"></path></svg>
            </div>
            <div>
              <h1 className="text-base font-semibold text-gray-100 tracking-tight">
                Tro ly tai lieu noi bo
              </h1>
              <p className="text-xs text-gray-400 font-medium">
                Du lieu noi bo & RAG Engine
              </p>
            </div>
          </div>
          <button
            onClick={newChat}
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-2 text-xs font-medium text-gray-200 transition-all hover:bg-white/10 hover:border-white/20 md:hidden"
            disabled={busy}
          >
            Chat moi
          </button>
        </header>

        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto scroll-smooth">
          <div className="mx-auto flex max-w-4xl flex-col gap-6 px-4 py-8">
            {messages.length === 0 && (
              <div className="mt-20 flex flex-col items-center justify-center text-center animate-msg">
                <div className="mb-6 flex h-20 w-20 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-500/10 to-teal-500/10 border border-emerald-500/20 shadow-[0_0_30px_rgba(16,185,129,0.15)]">
                   <svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 24 24" fill="none" stroke="url(#emerald-gradient)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><defs><linearGradient id="emerald-gradient" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" stopColor="#34d399" /><stop offset="100%" stopColor="#14b8a6" /></linearGradient></defs><path d="M12 8V4H8"></path><rect width="16" height="12" x="4" y="8" rx="2"></rect><path d="M2 14h2"></path><path d="M20 14h2"></path><path d="M15 13v2"></path><path d="M9 13v2"></path></svg>
                </div>
                <h2 className="text-2xl font-semibold bg-gradient-to-r from-emerald-400 to-teal-200 bg-clip-text text-transparent">Xin chao, toi co the giup gi cho ban?</h2>
                <p className="mt-3 text-sm text-gray-400 max-w-md leading-relaxed">
                  Dat cau hoi ve tai lieu, quy trinh, chinh sach hoac du lieu noi bo. Toi se tim kiem va tra loi ban dua tren co so du lieu cua chung ta.
                </p>
              </div>
            )}

            {messages.map((m, i) => (
              <div
                key={`${m.role}-${i}`}
                className={`animate-msg ${m.role === "user" ? "flex justify-end" : "flex justify-start"}`}
              >
                <div
                  className={
                    m.role === "user"
                      ? "max-w-[85%] rounded-2xl rounded-tr-sm bg-gradient-to-br from-neutral-800 to-neutral-900 border border-white/5 px-5 py-4 text-gray-100 shadow-md"
                      : "max-w-[85%] rounded-2xl rounded-tl-sm bg-neutral-900/60 backdrop-blur-md border border-white/5 px-5 py-4 text-gray-100 shadow-sm"
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
                    <div className="mt-4 flex gap-2 text-xs border-t border-white/5 pt-3">
                      <button
                        onClick={() => sendFeedback(m.chatId as number, 1)}
                        className={`rounded-lg border px-2.5 py-1.5 transition-all flex items-center gap-1.5 ${
                          m.feedback === 1
                            ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-400"
                            : "border-white/5 bg-white/5 text-gray-400 hover:text-gray-200 hover:bg-white/10"
                        }`}
                      >
                        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"></path></svg>
                        Huu ich
                      </button>
                      <button
                        onClick={() => sendFeedback(m.chatId as number, -1)}
                        className={`rounded-lg border px-2.5 py-1.5 transition-all flex items-center gap-1.5 ${
                          m.feedback === -1
                            ? "border-red-500/50 bg-red-500/10 text-red-400"
                            : "border-white/5 bg-white/5 text-gray-400 hover:text-gray-200 hover:bg-white/10"
                        }`}
                      >
                        <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3zm7-13h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"></path></svg>
                        Khong tot
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

        <footer className="z-10 shrink-0 px-4 pb-6 pt-2 bg-gradient-to-t from-[#050505] via-[#050505]/80 to-transparent">
          <div className="mx-auto max-w-4xl relative">
            {selectedFile ? (
              <div className="absolute -top-12 left-0 right-0 flex items-center justify-between rounded-xl border border-emerald-500/30 bg-neutral-900/90 backdrop-blur-md px-4 py-2.5 text-sm text-gray-200 shadow-lg animate-msg">
                <div className="flex items-center gap-2 truncate">
                  <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-emerald-400"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="12" y1="18" x2="12" y2="12"></line><line x1="9" y1="15" x2="15" y2="15"></line></svg>
                  <span className="truncate">{selectedFile.name}</span>
                </div>
                <button
                  onClick={clearSelectedFile}
                  className="ml-3 p-1 text-gray-400 hover:text-red-400 hover:bg-white/10 rounded-lg transition-colors"
                  disabled={busy}
                  title="Bo file"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
              </div>
            ) : null}
            <div className="flex items-end gap-2 rounded-2xl border border-white/10 bg-neutral-900/80 backdrop-blur-2xl p-2 shadow-2xl transition-all focus-within:border-emerald-500/40 focus-within:ring-1 focus-within:ring-emerald-500/40">
              <label className="flex h-11 w-11 cursor-pointer items-center justify-center rounded-xl text-gray-400 transition hover:bg-white/10 hover:text-gray-200" title="Tai len">
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>
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
                placeholder="Nhap tin nhan vao day..."
                className="max-h-40 flex-1 resize-none bg-transparent px-2 py-2.5 text-[15px] text-gray-100 outline-none placeholder:text-gray-500"
              />
              <button
                onClick={send}
                disabled={busy || !input.trim()}
                className="flex h-11 w-11 items-center justify-center rounded-xl bg-gradient-to-r from-emerald-500 to-teal-500 text-neutral-950 transition-all hover:from-emerald-400 hover:to-teal-400 disabled:cursor-not-allowed disabled:opacity-40 disabled:from-gray-700 disabled:to-gray-700 disabled:text-gray-400 hover:shadow-[0_0_15px_rgba(16,185,129,0.4)]"
                title="Gui"
              >
                {busy ? (
                   <svg className="animate-spin h-5 w-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                ) : (
                  <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><line x1="22" y1="2" x2="11" y2="13"></line><polygon points="22 2 15 22 11 13 2 9 22 2"></polygon></svg>
                )}
              </button>
            </div>
            <p className="mt-3 text-center text-xs font-medium text-gray-500/70">
              Upload trong chat chi ho tro hinh anh.
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
