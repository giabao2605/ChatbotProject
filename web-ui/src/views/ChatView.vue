<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from "vue";
import * as api from "@/api/client";
import { useChatStore } from "@/stores/chat";
import type { ChatMessage, SessionItem } from "@/types";

const chatStore = useChatStore();
const sessions = ref<SessionItem[]>([]);
const messages = ref<ChatMessage[]>([]);
const input = ref("");
const busy = ref(false);
const thinking = ref(false);
const warning = ref("");
const error = ref("");
const selectedFile = ref<File | null>(null);
const selectedPreview = ref("");
const uploadToken = ref("");
const scrollEl = ref<HTMLElement | null>(null);

const sessionId = computed(() => chatStore.sessionId);

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
  messages.value.push(userMessage, { role: "assistant", content: "" });
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
        },
        onDelta(text) {
          thinking.value = false;
          const last = messages.value[messages.value.length - 1];
          if (last?.role === "assistant") last.content += text;
          scrollToBottom();
        },
        onWarning(message) {
          warning.value = message;
        },
        onError(message) {
          error.value = message;
          const last = messages.value[messages.value.length - 1];
          if (last?.role === "assistant" && !last.content) last.content = `Lỗi: ${message}`;
        },
        onDone(data) {
          thinking.value = false;
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
    <aside class="chat-history">
      <Button label="Cuộc trò chuyện mới" class="full-width" @click="newChat" />
      <div class="history-list">
        <div v-for="session in sessions" :key="session.session_id" class="history-row">
          <button class="history-title" @click="openSession(session.session_id)">
            {{ session.cau_hoi }}
          </button>
          <button class="text-button" @click="removeSession(session.session_id)">Xóa</button>
        </div>
      </div>
    </aside>

    <section class="chat-main">
      <header class="page-header">
        <div>
          <div class="eyebrow">RAG Q&A</div>
          <h1>Trợ lý tài liệu nội bộ</h1>
        </div>
        <Tag :value="busy ? 'Đang xử lý' : 'Sẵn sàng'" :severity="busy ? 'warn' : 'success'" />
      </header>

      <div ref="scrollEl" class="message-scroll">
        <div v-if="messages.length === 0" class="empty-state">
          <h2>Xin chào, tôi có thể giúp gì cho bạn?</h2>
          <p>Đặt câu hỏi về tài liệu, quy trình, chính sách hoặc dữ liệu nội bộ.</p>
        </div>

        <article v-for="(message, index) in messages" :key="`${message.role}-${index}`" :class="['message', message.role]">
          <div class="message-card">
            <div class="message-label">{{ message.role === "user" ? "Bạn" : "Trợ lý" }}</div>
            <img v-if="message.image_url" :src="message.image_url" :alt="message.image_name || 'Ảnh upload'" class="upload-preview" />
            <div v-if="message.role === 'assistant' && !message.content && thinking" class="thinking-row">
              <ProgressSpinner stroke-width="4" class="small-spinner" />
              <span>Đang suy nghĩ và tra cứu tài liệu...</span>
            </div>
            <p v-else class="message-text">{{ message.content }}</p>

            <details v-if="message.ref_text" class="source-block">
              <summary>Nguồn tham khảo</summary>
              <pre>{{ message.ref_text }}</pre>
            </details>

            <div v-if="message.citations?.length" class="citation-grid">
              <a v-for="citation in message.citations" :key="`${citation.doc_id}-${citation.page_no}`" :href="citation.original_url" target="_blank" class="citation-card">
                <img :src="citation.page_url" :alt="`Doc ${citation.doc_id} trang ${citation.page_no}`" />
                <span>{{ citation.file_name || `Doc ${citation.doc_id}` }} · trang {{ citation.page_no }}</span>
              </a>
            </div>

            <div v-if="message.role === 'assistant' && message.chat_id" class="feedback-row">
              <span>Đánh giá câu trả lời</span>
              <Button label="Hữu ích" size="small" :outlined="message.feedback !== 1" @click="rate(message, 1)" />
              <Button label="Không tốt" size="small" severity="secondary" :outlined="message.feedback !== -1" @click="rate(message, -1)" />
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
          <Textarea v-model="input" auto-resize rows="1" placeholder="Hỏi bất cứ điều gì" @keydown.enter.exact.prevent="send" />
          <Button label="Gửi" :loading="busy" :disabled="!input.trim()" @click="send" />
        </div>
      </footer>
    </section>
  </div>
</template>
