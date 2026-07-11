<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import * as api from "@/api/client";
import { useChatStore } from "@/stores/chat";
import { renderMarkdown } from "@/utils/markdown";
import type { ChatMessage, SessionItem } from "@/types";

type UiMessage = ChatMessage & {
  progress?: string[];
  progressState?: "running" | "done" | "error";
};

const chatStore = useChatStore();
const sessions = ref<SessionItem[]>([]);
const messages = ref<UiMessage[]>([]);
const input = ref("");
const busy = ref(false);
const thinking = ref(false);
const historyOpen = ref(false);
const warning = ref("");
const error = ref("");
const selectedFile = ref<File | null>(null);
const selectedPreview = ref("");
const uploadToken = ref("");
const scrollEl = ref<HTMLElement | null>(null);

const sessionId = computed(() => chatStore.sessionId);
const latestAssistant = computed(() => [...messages.value].reverse().find((message) => message.role === "assistant"));

async function refreshSessions() {
  sessions.value = await api.listSessions();
}

async function scrollToBottom() {
  await nextTick();
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
}

async function newChat() {
  if (busy.value) return;
  chatStore.newSession();
  messages.value = [];
  warning.value = "";
  error.value = "";
  clearFile();
}

async function openSession(id: string) {
  if (busy.value) return;
  chatStore.openSession(id);
  messages.value = await api.loadHistory(id);
  historyOpen.value = false;
  await scrollToBottom();
}

async function removeSession(id: string) {
  await api.deleteSession(id);
  if (id === sessionId.value) await newChat();
  await refreshSessions();
}

function clearFile() {
  if (selectedPreview.value) URL.revokeObjectURL(selectedPreview.value);
  selectedFile.value = null;
  selectedPreview.value = "";
  uploadToken.value = "";
}

function setAssistantProgress(step: string, state: UiMessage["progressState"] = "running") {
  const last = messages.value[messages.value.length - 1];
  if (last?.role !== "assistant") return;
  const current = last.progress ?? [];
  if (!current.includes(step)) current.push(step);
  last.progress = current;
  last.progressState = state;
}

async function onFileChange(event: Event) {
  clearFile();
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  selectedFile.value = file;
  selectedPreview.value = URL.createObjectURL(file);
  const uploaded = await api.uploadChatImage(file);
  uploadToken.value = uploaded.image_token;
}

async function send() {
  const question = input.value.trim();
  if (!question || busy.value) return;
  error.value = "";
  warning.value = "";
  busy.value = true;
  thinking.value = false;
  input.value = "";

  const currentSession = sessionId.value;
  const history = messages.value.map((message) => ({
    role: message.role,
    content: message.content,
  }));
  const userMessage: ChatMessage = {
    role: "user",
    content: question,
    image_url: selectedPreview.value || null,
    image_name: selectedFile.value?.name || null,
  };
  messages.value.push(userMessage, {
    role: "assistant",
    content: "",
    progress: ["Đã nhận câu hỏi"],
    progressState: "running",
  });
  const imageToken = uploadToken.value || null;
  clearFile();
  await scrollToBottom();

  const memory = chatStore.currentMemory;
  try {
    await api.sendChatMessage(
      {
        session_id: currentSession,
        question,
        image_token: imageToken,
        chat_history: history,
        current_part_ids: memory.currentPartIds,
        conversation_context: memory.conversationContext,
      },
      {
        onThinking() {
          thinking.value = true;
          setAssistantProgress("Đang phân tích ý định và tra cứu tài liệu");
          scrollToBottom();
        },
        onDelta(text) {
          thinking.value = false;
          setAssistantProgress("Đang soạn câu trả lời từ tài liệu");
          const last = messages.value[messages.value.length - 1];
          if (last?.role === "assistant") last.content += text;
          scrollToBottom();
        },
        onWarning(message) {
          warning.value = message;
        },
        onError(message) {
          error.value = message;
          setAssistantProgress("Có lỗi khi xử lý câu hỏi", "error");
          const last = messages.value[messages.value.length - 1];
          if (last?.role === "assistant" && !last.content) last.content = `Lỗi: ${message}`;
        },
        onDone(data) {
          thinking.value = false;
          setAssistantProgress("Hoàn tất", "done");
          const last = messages.value[messages.value.length - 1];
          if (last?.role === "assistant") {
            last.chat_id = data.chat_id ?? null;
            last.ref_text = data.ref_text || "";
            last.citations = data.citations ?? [];
          }
          chatStore.updateMemory(currentSession, {
            currentPartIds: data.new_part_ids ?? [],
            conversationContext: data.conversation_context ?? null,
          });
          refreshSessions();
        },
      },
    );
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Gửi câu hỏi thất bại";
    setAssistantProgress("Có lỗi khi gửi câu hỏi", "error");
  } finally {
    busy.value = false;
    thinking.value = false;
  }
}

async function rate(message: ChatMessage, rating: number) {
  if (!message.chat_id) return;
  await api.sendFeedback(message.chat_id, rating);
  message.feedback = rating;
}

onMounted(async () => {
  await refreshSessions();
});
</script>

<template>
  <div class="chat-page">
    <section class="chat-main">
      <header class="chat-header">
        <div>
          <div class="eyebrow">RAG Q&A</div>
          <h1>Trợ lý tài liệu nội bộ</h1>
          <p class="page-subtitle">Hỏi đáp theo tài liệu, có nguồn tham khảo và ảnh đính kèm khi cần.</p>
        </div>
        <div class="chat-header-actions">
          <Button label="Cuộc trò chuyện mới" severity="secondary" outlined :disabled="busy" @click="newChat" />
          <Button
            :label="historyOpen ? 'Ẩn lịch sử' : 'Lịch sử'"
            severity="secondary"
            outlined
            @click="historyOpen = !historyOpen"
          />
          <Tag :value="busy ? 'Đang xử lý' : 'Sẵn sàng'" :severity="busy ? 'warn' : 'success'" />
        </div>
      </header>

      <div :class="['chat-body', { 'with-history': historyOpen }]">
        <div class="chat-column">
          <div ref="scrollEl" class="message-scroll">
            <div v-if="messages.length === 0" class="empty-state">
              <h2>Xin chào, tôi có thể giúp gì cho bạn?</h2>
              <p>Đặt câu hỏi về tài liệu, quy trình, chính sách hoặc dữ liệu nội bộ.</p>
              <div class="prompt-grid">
                <button type="button" @click="input = 'Tóm tắt quy trình mới nhất của phòng Production'">
                  Tóm tắt quy trình theo phòng ban
                </button>
                <button type="button" @click="input = 'So sánh hai tài liệu liên quan tới chính sách nghỉ phép'">
                  So sánh nội dung giữa tài liệu
                </button>
                <button type="button" @click="input = 'Tìm bảng lương tháng 6 và nêu các điểm cần chú ý'">
                  Tra cứu tài liệu cụ thể
                </button>
              </div>
            </div>

            <article
              v-for="(message, index) in messages"
              :key="`${message.role}-${index}`"
              :class="['message', message.role]"
            >
              <div class="message-card">
                <div class="message-label">{{ message.role === "user" ? "Bạn" : "Trợ lý" }}</div>
                <img
                  v-if="message.image_url"
                  :src="message.image_url"
                  :alt="message.image_name || 'Ảnh upload'"
                  class="upload-preview"
                />
                <div v-if="message.role === 'assistant' && !message.content" class="thinking-row">
                  <div class="typing-dots" aria-label="Đang trả lời">
                    <span></span>
                    <span></span>
                    <span></span>
                  </div>
                  <span>Đang trả lời</span>
                </div>
                <div
                  v-else-if="message.role === 'assistant'"
                  class="message-content"
                  v-html="renderMarkdown(message.content)"
                ></div>
                <p v-else class="message-text">{{ message.content }}</p>

                <ol v-if="message.role === 'assistant' && message.progress?.length" class="progress-list">
                  <li
                    v-for="step in message.progress"
                    :key="step"
                    :class="{
                      active: message.progressState === 'running' && step === message.progress[message.progress.length - 1],
                      done: message.progressState === 'done',
                      error: message.progressState === 'error' && step === message.progress[message.progress.length - 1],
                    }"
                  >
                    <span v-text="step"></span>
                  </li>
                </ol>

                <details v-if="message.ref_text" class="source-block">
                  <summary>Nguồn tham khảo</summary>
                  <pre>{{ message.ref_text }}</pre>
                </details>

                <div v-if="message.citations?.length" class="citation-grid">
                  <article
                    v-for="citation in message.citations"
                    :key="`${citation.doc_id}-${citation.page_no}`"
                    class="citation-card"
                  >
                    <a
                      v-if="citation.has_vision && citation.page_url"
                      :href="citation.page_url"
                      target="_blank"
                      class="citation-preview-link"
                      :aria-label="`Mở ảnh nguồn ${citation.file_name || citation.doc_id}, trang ${citation.page_no}`"
                    >
                      <img :src="citation.page_url" :alt="`Doc ${citation.doc_id} trang ${citation.page_no}`" />
                    </a>
                    <div class="citation-card-body">
                      <span>
                        {{ citation.file_name || `Doc ${citation.doc_id}` }} · trang {{ citation.page_no }}
                        <template v-if="citation.version_no !== undefined && citation.version_no !== null">
                          · version {{ citation.version_no }}
                        </template>
                      </span>
                      <a
                        :href="citation.original_url"
                        class="citation-download"
                        target="_blank"
                        rel="noopener"
                      >
                        Tải bản gốc
                      </a>
                    </div>
                  </article>
                </div>

                <div v-if="message.role === 'assistant' && message.chat_id" class="feedback-row">
                  <span>Đánh giá câu trả lời</span>
                  <Button label="Hữu ích" size="small" :outlined="message.feedback !== 1" @click="rate(message, 1)" />
                  <Button
                    label="Không tốt"
                    size="small"
                    severity="secondary"
                    :outlined="message.feedback !== -1"
                    @click="rate(message, -1)"
                  />
                </div>
              </div>
            </article>
          </div>

          <Message v-if="warning" severity="warn">{{ warning }}</Message>
          <Message v-if="error" severity="error">{{ error }}</Message>

          <footer class="composer">
            <div v-if="selectedPreview" class="selected-file">
              <img :src="selectedPreview" alt="Ảnh đã chọn" />
              <span>{{ selectedFile?.name }}</span>
              <Button label="Bỏ file" severity="secondary" text @click="clearFile" />
            </div>
            <div class="composer-row">
              <label class="file-button">
                Đính kèm
                <input type="file" accept=".png,.jpg,.jpeg,.bmp,.gif,.webp,.tif,.tiff" @change="onFileChange" />
              </label>
              <Textarea
                v-model="input"
                auto-resize
                rows="1"
                placeholder="Hỏi bất cứ điều gì"
                @keydown.enter.exact.prevent="send"
              />
              <Button :label="busy ? 'Đang gửi' : 'Gửi'" :disabled="busy || !input.trim()" @click="send" />
            </div>
          </footer>
        </div>

        <aside v-if="historyOpen" class="history-panel">
          <div class="history-panel-header">
            <strong>Lịch sử trò chuyện</strong>
            <Button label="Tạo mới" size="small" @click="newChat" />
          </div>
          <div class="history-list">
            <div v-for="session in sessions" :key="session.session_id" class="history-row">
              <button class="history-title" @click="openSession(session.session_id)">
                {{ session.cau_hoi }}
              </button>
              <button class="text-button" @click="removeSession(session.session_id)">Xóa</button>
            </div>
            <p v-if="!sessions.length" class="muted-text">Chưa có lịch sử.</p>
          </div>
        </aside>
      </div>
    </section>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  height: 100dvh;
  overflow: hidden;
}
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border-bottom: 1px solid var(--border);
  padding: 1rem clamp(1rem, 2vw, 2rem);
}
.chat-header h1 {
  margin: 0.2rem 0 0;
  font-size: 1.2rem;
}
.chat-header-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 0.6rem;
}
.chat-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 1rem;
  flex: 1;
  min-height: 0;
  padding: 1rem clamp(1rem, 2vw, 2rem);
}
.chat-body.with-history {
  grid-template-columns: minmax(0, 1fr) minmax(280px, 340px);
}
.chat-column {
  display: flex;
  min-width: 0;
  min-height: 0;
  flex-direction: column;
  width: min(100%, 1080px);
  margin: 0 auto;
}
.with-history .chat-column {
  width: 100%;
}
.message-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(11, 18, 32, 0.48);
  padding: clamp(1rem, 2vw, 1.5rem);
}
.empty-state {
  display: grid;
  gap: 0.85rem;
  width: min(760px, 100%);
  margin: 10vh auto 0;
  text-align: center;
}
.empty-state h2 {
  margin: 0;
  font-size: 1.35rem;
}
.empty-state p {
  margin: 0;
  color: var(--muted);
}
.prompt-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 0.75rem;
  margin-top: 0.5rem;
}
.prompt-grid button {
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  padding: 0.85rem;
  text-align: left;
}
.prompt-grid button:hover {
  border-color: var(--accent);
}
.message {
  display: flex;
  margin: 0 auto 1rem;
  width: min(920px, 100%);
}
.message.user {
  justify-content: flex-end;
}
.message-card {
  max-width: min(760px, 92%);
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(23, 32, 51, 0.78);
  padding: 0.85rem 1rem;
}
.message.assistant .message-card {
  background: rgba(15, 23, 42, 0.72);
}
.message.user .message-card {
  background: var(--surface-strong);
}
.message-label {
  color: var(--faint);
  font-size: 0.75rem;
  font-weight: 760;
  margin-bottom: 0.4rem;
}
.message.user .message-label {
  text-align: right;
}
.message-text {
  line-height: 1.7;
  margin: 0;
  white-space: pre-wrap;
}
.message-content {
  display: grid;
  gap: 0.75rem;
  line-height: 1.65;
}
.message-content :deep(p),
.message-content :deep(ul),
.message-content :deep(ol) {
  margin: 0;
}
.message-content :deep(ul),
.message-content :deep(ol) {
  padding-left: 1.25rem;
}
.message-content :deep(li + li) {
  margin-top: 0.25rem;
}
.message-content :deep(h3),
.message-content :deep(h4),
.message-content :deep(h5) {
  margin: 0.45rem 0 0;
  color: #e5edf8;
  font-size: 0.98rem;
}
.message-content :deep(code) {
  border: 1px solid var(--border);
  border-radius: 5px;
  background: rgba(148, 163, 184, 0.12);
  padding: 0.05rem 0.3rem;
}
.message-content :deep(.md-table-wrap) {
  max-width: 100%;
  overflow-x: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.message-content :deep(table) {
  width: 100%;
  border-collapse: collapse;
  table-layout: auto;
  font-size: 0.86rem;
}
.message-content :deep(th),
.message-content :deep(td) {
  border-bottom: 1px solid rgba(148, 163, 184, 0.18);
  padding: 0.55rem 0.65rem;
  text-align: left;
  vertical-align: top;
}
.message-content :deep(th) {
  background: rgba(56, 189, 248, 0.12);
  color: #dff6ff;
  font-weight: 760;
  white-space: nowrap;
}
.message-content :deep(td) {
  color: var(--text);
  overflow-wrap: anywhere;
}
.message-content :deep(tbody tr:nth-child(even)) {
  background: rgba(148, 163, 184, 0.05);
}
.thinking-row {
  display: flex;
  align-items: center;
  gap: 0.65rem;
  color: var(--muted);
}
.typing-dots {
  display: inline-flex;
  align-items: center;
  gap: 0.28rem;
  height: 1rem;
}
.typing-dots span {
  width: 0.42rem;
  height: 0.42rem;
  border-radius: 999px;
  background: var(--accent);
  animation: dotPulse 1.1s infinite ease-in-out;
}
.typing-dots span:nth-child(2) {
  animation-delay: 0.16s;
}
.typing-dots span:nth-child(3) {
  animation-delay: 0.32s;
}
@keyframes dotPulse {
  0%, 80%, 100% {
    opacity: 0.35;
    transform: translateY(0);
  }
  40% {
    opacity: 1;
    transform: translateY(-3px);
  }
}
.progress-list {
  display: grid;
  gap: 0.4rem;
  margin: 0.75rem 0 0;
  padding: 0;
  list-style: none;
}
.progress-list li {
  position: relative;
  padding-left: 1rem;
  color: var(--faint);
  font-size: 0.82rem;
}
.progress-list li::before {
  position: absolute;
  top: 0.45rem;
  left: 0;
  width: 0.45rem;
  height: 0.45rem;
  border-radius: 999px;
  background: var(--border);
  content: "";
}
.progress-list li.active {
  color: var(--text);
}
.progress-list li.active::before {
  background: var(--accent);
  animation: dotPulse 1.1s infinite ease-in-out;
}
.progress-list li.done::before {
  background: var(--accent);
}
.progress-list li.error {
  color: #fca5a5;
}
.progress-list li.error::before {
  background: #f87171;
}
.composer {
  flex-shrink: 0;
  margin-top: 0.85rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(23, 32, 51, 0.88);
  padding: 0.75rem;
}
.composer-row,
.selected-file,
.feedback-row {
  display: flex;
  align-items: center;
  gap: 0.7rem;
}
.composer .p-textarea {
  flex: 1;
}
.file-button {
  border: 1px solid var(--action-border);
  border-radius: 8px;
  background: rgba(56, 189, 248, 0.09);
  color: #aee7ff;
  cursor: pointer;
  padding: 0.65rem 0.75rem;
  white-space: nowrap;
}
.file-button:hover {
  background: var(--action-hover);
  color: #ffffff;
}
.file-button input {
  display: none;
}
.selected-file {
  color: var(--muted);
  margin-bottom: 0.65rem;
}
.selected-file img {
  width: 44px;
  height: 44px;
  border-radius: 8px;
  object-fit: cover;
}
.feedback-row {
  flex-wrap: wrap;
  margin-top: 0.75rem;
}
.history-panel {
  align-self: start;
  position: sticky;
  top: 1rem;
  max-height: calc(100dvh - 114px);
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(23, 32, 51, 0.86);
  padding: 0.9rem;
}
.history-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.85rem;
}
.history-list {
  display: grid;
  gap: 0.5rem;
}
.history-row {
  border: 1px solid transparent;
  border-radius: 8px;
  padding: 0.65rem;
}
.history-row:hover {
  background: var(--surface);
}
.history-title,
.text-button {
  border: 0;
  background: transparent;
  color: var(--text);
  cursor: pointer;
  padding: 0;
  text-align: left;
}
.history-title {
  width: 100%;
}
.text-button {
  color: #fca5a5;
  font-size: 0.75rem;
  margin-top: 0.35rem;
}
.text-button:hover {
  color: #ffffff;
}
@media (max-width: 1180px) {
  .chat-body.with-history {
    grid-template-columns: 1fr;
  }
  .history-panel {
    position: static;
    max-height: 260px;
    order: -1;
  }
}
@media (max-width: 720px) {
  .chat-header,
  .composer-row {
    align-items: stretch;
    flex-direction: column;
  }
  .chat-header-actions {
    justify-content: flex-start;
  }
  .composer-row .p-textarea,
  .composer-row .p-button,
  .file-button {
    width: 100%;
  }
  .message-card {
    max-width: 100%;
  }
}
</style>
