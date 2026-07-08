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

type Lang = "vi" | "en";

const COPY = {
  vi: {
    newChat: "Cuộc trò chuyện mới",
    searchHistory: "Tìm kiếm lịch sử...",
    noMatchingHistory: "Chưa có lịch sử phù hợp.",
    delete: "Xóa",
    title: "Trợ lý tài liệu nội bộ",
    subtitle: "Dữ liệu nội bộ & RAG Engine",
    sidebarPlan: "RAG Q&A",
    mobileNewChat: "Chat mới",
    qaMode: "Hỏi đáp tài liệu",
    readyStatus: "Sẵn sàng",
    busyStatus: "Đang tra cứu",
    recents: "Gần đây",
    searchChats: "Tìm kiếm cuộc trò chuyện",
    conversationCount: "{count} cuộc trò chuyện",
    currentChat: "Đang mở",
    accountLabel: "Tài khoản",
    appStatus: "Sẵn sàng",
    assistantLabel: "Trợ lý",
    userLabel: "Bạn",
    promptTitle: "Gợi ý câu hỏi",
    sourceSummary: "Mở đoạn nguồn đã truy xuất",
    feedbackPrompt: "Đánh giá câu trả lời",
    typing: "Đang tìm trong tài liệu và soạn câu trả lời",
    attachPreview: "File đã chọn",
    typeHint: "Enter để gửi, Shift Enter để xuống dòng.",
    sending: "Đang gửi",
    greeting: "Xin chào, tôi có thể giúp gì cho bạn?",
    intro:
      "Đặt câu hỏi về tài liệu, quy trình, chính sách hoặc dữ liệu nội bộ. Tôi sẽ tìm kiếm và trả lời bạn dựa trên cơ sở dữ liệu của chúng ta.",
    missingContext:
      "Thiếu thông tin phiên đăng nhập. Hãy mở chat từ tab Chatbot trong Streamlit.",
    warning: "Cảnh báo",
    unknownError: "Lỗi không xác định",
    errorPrefix: "Lỗi",
    uploadedImageAlt: "Ảnh upload",
    filePrefix: "File",
    references: "Nguồn tham khảo",
    helpful: "Hữu ích",
    notGood: "Không tốt",
    removeFile: "Bỏ file",
    upload: "Đính kèm",
    inputPlaceholder: "Hỏi bất cứ điều gì",
    send: "Gửi",
    imageOnlyUpload: "Upload trong chat chỉ hỗ trợ hình ảnh.",
  },
  en: {
    newChat: "New chat",
    searchHistory: "Search chats",
    noMatchingHistory: "No matching history.",
    delete: "Delete",
    title: "Internal Document Assistant",
    subtitle: "Internal data & RAG Engine",
    sidebarPlan: "RAG Q&A",
    mobileNewChat: "New chat",
    qaMode: "Document Q&A",
    readyStatus: "Ready",
    busyStatus: "Searching",
    recents: "Recents",
    searchChats: "Search chats",
    conversationCount: "{count} conversations",
    currentChat: "Current",
    accountLabel: "Account",
    appStatus: "Ready",
    assistantLabel: "Assistant",
    userLabel: "You",
    promptTitle: "Suggested questions",
    sourceSummary: "Open retrieved source text",
    feedbackPrompt: "Rate this answer",
    typing: "Searching documents and drafting the answer",
    attachPreview: "Selected file",
    typeHint: "Enter to send, Shift Enter for a new line.",
    sending: "Sending",
    greeting: "How can I help you?",
    intro:
      "Ask about documents, processes, policies, or internal data. I will search and answer based on our knowledge base.",
    missingContext:
      "Missing login session information. Open chat from the Chatbot tab in Streamlit.",
    warning: "Warning",
    unknownError: "Unknown error",
    errorPrefix: "Error",
    uploadedImageAlt: "Uploaded image",
    filePrefix: "File",
    references: "References",
    helpful: "Helpful",
    notGood: "Not good",
    removeFile: "Remove file",
    upload: "Attach",
    inputPlaceholder: "Ask anything",
    send: "Send",
    imageOnlyUpload: "Chat upload only supports images.",
  },
} satisfies Record<Lang, Record<string, string>>;

const SUGGESTIONS = {
  vi: [
    "Tóm tắt quy trình bảo trì theo tài liệu mới nhất",
    "Tìm thông tin về mã part hoặc vật tư này",
    "So sánh yêu cầu kiểm tra giữa hai tài liệu",
  ],
  en: [
    "Summarize the latest maintenance process",
    "Find information about this part or material code",
    "Compare inspection requirements across two documents",
  ],
} satisfies Record<Lang, string[]>;

function createSessionId() {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export default function ChatPage() {
  const [ctx, setCtx] = useState<string | null>(null);
  const [lang, setLang] = useState<Lang>("vi");
  const [embedded, setEmbedded] = useState(false);
  const [requestedSessionId, setRequestedSessionId] = useState<string | null>(null);
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
    setLang(params.get("lang")?.toLowerCase().startsWith("en") ? "en" : "vi");
    setEmbedded(params.get("embed") === "1");
    setRequestedSessionId(params.get("session"));
  }, []);

  const text = COPY[lang];
  const conversationCount = text.conversationCount.replace(
    "{count}",
    String(sessions.length),
  );

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = messages.length === 0 ? 0 : el.scrollHeight;
  }, [messages]);

  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [input]);

  useEffect(() => {
    if (!ctx) return;
    refreshSessions(ctx).catch((e) => setWarning((e as Error).message));
  }, [ctx]);

  useEffect(() => {
    if (!ctx || !requestedSessionId || busy || requestedSessionId === sessionId) {
      return;
    }
    loadSession(requestedSessionId);
  }, [ctx, requestedSessionId, busy, sessionId]);

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

  function appendToLast(textChunk: string) {
    setMessages((prev) => {
      const copy = [...prev];
      const last = copy[copy.length - 1];
      if (last && last.role === "assistant") {
        copy[copy.length - 1] = { ...last, content: last.content + textChunk };
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

  function suggestQuestion(question: string) {
    if (busy) return;
    setInput(question);
    window.setTimeout(() => taRef.current?.focus(), 0);
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
      setError(text.missingContext);
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
              `${data.message || text.warning}${
                data.detail ? `: ${data.detail}` : ""
              }`,
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
                text.unknownError,
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
            content: last.content || `${text.errorPrefix}: ${msg}`,
          };
        } else {
          copy.push({ role: "assistant", content: `${text.errorPrefix}: ${msg}` });
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

  const showEmptyState = messages.length === 0;

  return (
    <div className={embedded ? "chatgpt-shell embedded" : "chatgpt-shell"}>
      {!embedded ? (
      <aside className="chatgpt-sidebar">
        <div className="sidebar-top">
          <div className="sidebar-brand-row">
            <div className="sidebar-avatar">ID</div>
            <div className="sidebar-brand-copy">
              <div className="sidebar-brand">{text.title}</div>
              <div className="sidebar-subtitle">{text.sidebarPlan}</div>
            </div>
          </div>
          <button onClick={newChat} disabled={busy} className="sidebar-action">
            {text.newChat}
          </button>
          <label className="sidebar-search-box">
            <span>{text.searchChats}</span>
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={text.searchHistory}
              className="sidebar-search"
            />
          </label>
          <div className="sidebar-metrics">
            <span>{conversationCount}</span>
            <span>{text.appStatus}</span>
          </div>
        </div>

        <div className="sidebar-recents">
          <div className="sidebar-section-title">{text.recents}</div>
          {filteredSessions.map((s) => (
            <div
              key={s.session_id}
              className={
                s.session_id === sessionId
                  ? "session-row session-row-active"
                  : "session-row"
              }
            >
              <button
                onClick={() => loadSession(s.session_id)}
                disabled={busy}
                title={s.cau_hoi}
                className="session-title"
              >
                {s.cau_hoi}
              </button>
              <div className="session-meta">
                <span>{s.owner || text.accountLabel}</span>
                {s.session_id === sessionId ? <span>{text.currentChat}</span> : null}
              </div>
              <div className="session-actions">
                <button
                  onClick={() => deleteSession(s.session_id)}
                  disabled={busy}
                  className="session-delete"
                >
                  {text.delete}
                </button>
              </div>
            </div>
          ))}
          {filteredSessions.length === 0 ? (
            <p className="empty-sidebar">{text.noMatchingHistory}</p>
          ) : null}
        </div>
        <div className="sidebar-footer">
          <div className="sidebar-footer-avatar">AD</div>
          <div>
            <div className="sidebar-footer-title">{text.accountLabel}</div>
            <div className="sidebar-footer-subtitle">{text.subtitle}</div>
          </div>
        </div>
      </aside>
      ) : null}

      <main className="chatgpt-main" aria-busy={busy}>
        <div className="chat-topbar">
          <div>
            <div className="topbar-kicker">{text.qaMode}</div>
            <h1>{text.title}</h1>
          </div>
          <div className={busy ? "status-pill status-pill-busy" : "status-pill"}>
            {busy ? text.busyStatus : text.readyStatus}
          </div>
        </div>

        <div className="mobile-bar">
          <div className="mobile-title">{text.title}</div>
          <button onClick={newChat} disabled={busy} className="mobile-new">
            {text.mobileNewChat}
          </button>
        </div>

        <div ref={scrollRef} className="chat-scroll" role="log" aria-live="polite">
          <div className={showEmptyState ? "empty-stage" : "message-stage"}>
            {showEmptyState ? (
              <div className="empty-content">
                <div className="empty-kicker">{text.qaMode}</div>
                <h1>{text.greeting}</h1>
                <p>{text.intro}</p>
                <div className="suggestion-panel">
                  <div className="suggestion-title">{text.promptTitle}</div>
                  <div className="suggestion-grid">
                    {SUGGESTIONS[lang].map((item) => (
                      <button
                        key={item}
                        type="button"
                        onClick={() => suggestQuestion(item)}
                        disabled={busy}
                        className="suggestion-chip"
                      >
                        {item}
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            ) : null}

            {messages.map((m, i) => (
              <div
                key={`${m.role}-${i}`}
                className={m.role === "user" ? "message user" : "message assistant"}
              >
                <div className="message-inner">
                  <div className="message-label">
                    {m.role === "user" ? text.userLabel : text.assistantLabel}
                  </div>
                  {m.imageUrl ? (
                    <img
                      src={m.imageUrl}
                      alt={m.imageName || text.uploadedImageAlt}
                      className="message-image"
                    />
                  ) : m.imageName ? (
                    <div className="message-file">
                      {text.filePrefix}: {m.imageName}
                    </div>
                  ) : null}

                  {m.role === "assistant" && m.content === "" ? (
                    <div className="typing-block">
                      <TypingDots />
                      <span>{text.typing}</span>
                    </div>
                  ) : m.role === "assistant" ? (
                    <div className="md-body">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {m.content}
                      </ReactMarkdown>
                    </div>
                  ) : (
                    <div className="message-text">{m.content}</div>
                  )}

                  {m.refText ? (
                    <details className="references">
                      <summary>
                        <span>{text.references}</span>
                        <span>{text.sourceSummary}</span>
                      </summary>
                      <pre>{m.refText}</pre>
                    </details>
                  ) : null}

                  {m.role === "assistant" && m.chatId ? (
                    <div className="feedback-row">
                      <span>{text.feedbackPrompt}</span>
                      <button
                        onClick={() => sendFeedback(m.chatId as number, 1)}
                        className={m.feedback === 1 ? "feedback selected" : "feedback"}
                      >
                        {text.helpful}
                      </button>
                      <button
                        onClick={() => sendFeedback(m.chatId as number, -1)}
                        className={
                          m.feedback === -1 ? "feedback selected" : "feedback"
                        }
                      >
                        {text.notGood}
                      </button>
                    </div>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        </div>

        {(error || warning) && (
          <div className="notice-wrap">
            {error ? <div className="notice error">{error}</div> : null}
            {warning ? <div className="notice warning">{warning}</div> : null}
          </div>
        )}

        <footer className={showEmptyState ? "composer-wrap centered" : "composer-wrap"}>
          <div className="composer">
            {selectedFile ? (
              <div className="selected-file">
                {selectedPreview ? (
                  <img src={selectedPreview} alt={text.attachPreview} />
                ) : null}
                <div>
                  <span>{text.attachPreview}</span>
                  <strong>{selectedFile.name}</strong>
                </div>
                <button onClick={clearSelectedFile} disabled={busy}>
                  {text.removeFile}
                </button>
              </div>
            ) : null}
            <div className="composer-row">
              <label className="attach-button">
                {text.upload}
                <input
                  ref={fileRef}
                  type="file"
                  accept=".png,.jpg,.jpeg,.bmp,.gif,.webp,.tif,.tiff"
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
                placeholder={text.inputPlaceholder}
                className="composer-input"
                aria-label={text.inputPlaceholder}
              />
              <button
                onClick={send}
                disabled={busy || !input.trim()}
                className="send-button"
                title={text.send}
              >
                {busy ? text.sending : text.send}
              </button>
            </div>
          </div>
          <p className="composer-hint">
            <span>{text.typeHint}</span>
            <span>{text.imageOnlyUpload}</span>
          </p>
        </footer>
      </main>
    </div>
  );
}

function TypingDots() {
  return (
    <div className="typing-dots">
      <span />
      <span />
      <span />
    </div>
  );
}
