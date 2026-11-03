<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import * as api from "@/api/client";
import { useChatStore } from "@/stores/chat";
import { useAuthStore } from '@/stores/auth';
import { formatSourceText, renderMarkdown } from "@/utils/markdown";
import AnswerSources from '@/components/AnswerSources.vue';
import AdvancedRagActions from '@/components/AdvancedRagActions.vue';
import AdvancedRagResult from '@/components/AdvancedRagResult.vue';
import { applyScanProgress, mergeScanResult } from '@/utils/chatScan';
import type { ChatMessage, Citation, SearchMode, SearchProgress, QueryActionState, SessionItem } from "@/types";

type UiMessage = ChatMessage & {
  progress?: string[];
  progressState?: "running" | "done" | "error";
  resumePayload?: Record<string, unknown>;
};

const activeSessionKey = "chat-active-session";

const chatStore = useChatStore();
const authStore = useAuthStore();
const sessions = ref<SessionItem[]>([]);
const messages = ref<UiMessage[]>([]);
const input = ref("");
const searchMode = ref<SearchMode>('quick');
const composerEl = ref<HTMLElement | null>(null);
let activeController: AbortController | undefined;
const actionState = ref<QueryActionState>({active:false,valid:true});
const busy = ref(false);
const thinking = ref(false);
const historyLoading = ref(false);
const historyFailed = ref(false);
const waitingSession = ref<string | null>(null);
const uploadLoading = ref(false);
const catalogLoading = ref(false);
const catalogError = ref('');
const historyOpen = ref(false);
const warning = ref("");
const error = ref("");
const fileInput = ref<HTMLInputElement | null>(null);
const selectedFile = ref<File | null>(null);
const selectedPreview = ref("");
const uploadToken = ref("");
const scrollEl = ref<HTMLElement | null>(null);
let contentSerial = 0;
let catalogSerial = 0;
let uploadSerial = 0;
const identity = computed(()=>JSON.stringify([authStore.user?.user_id,authStore.user?.username,
  authStore.user?.roles,authStore.user?.allowed_departments,authStore.user?.allowed_sites,authStore.user?.max_security_level]));

const sessionId = computed(() => chatStore.sessionId);
const latestAssistant = computed(() => [...messages.value].reverse().find((message) => message.role === "assistant"));

function invalidateSourceMessage(target:UiMessage) {
  warning.value = 'Nguồn đã thay đổi hoặc bạn không còn quyền xem. Hãy hỏi lại để lấy căn cứ hiện hành.';
  messages.value = messages.value.map(message => message === target ? {
    ...message,content:'Nội dung đã được ẩn vì không còn xác minh được nguồn.',citations:[],ref_text:'',advanced_rag:undefined,
    source_groups:[],verification_status:undefined,answer_outcome:undefined,resumePayload:undefined,
    search_progress:message.search_progress ? {search_mode:'comprehensive',scan_status:'denied'} : undefined,
  } : message);
}

async function refreshSessions() {
  const request=++catalogSerial, actor=identity.value;
  catalogLoading.value=true;catalogError.value='';
  try {
    const loaded=await api.listSessions();
    if (request===catalogSerial && actor===identity.value) sessions.value=loaded;
  } catch {
    if (request===catalogSerial && actor===identity.value) catalogError.value='Không tải được danh sách lịch sử. Hãy thử lại.';
  } finally {
    if (request===catalogSerial && actor===identity.value) catalogLoading.value=false;
  }
}

async function scrollToBottom() {
  await nextTick();
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight;
}

async function newChat() {
  if (busy.value) return;
  chatStore.newSession();
  sessionStorage.removeItem(activeSessionKey);
  resetView();
}

function resetView() {
  activeController?.abort();activeController=undefined;
  contentSerial++;
  historyLoading.value=false;historyFailed.value=false;waitingSession.value=null;busy.value=false;thinking.value=false;
  releaseMessagePreviews(messages.value);
  messages.value=[];warning.value='';error.value='';
  clearFile();
}

async function openSession(id: string) {
  if (busy.value) return;
  chatStore.openSession(id);
  resetView();
  const request=contentSerial, actor=identity.value;
  const current=()=>request===contentSerial && actor===identity.value && sessionId.value===id;
  historyLoading.value=true;
  if (chatStore.pendingSessionIds.includes(id)) {
    waitingSession.value=id;
    return;
  }
  try {
    const loaded=await api.loadHistory(id);
    if (!current()) return;
    messages.value=loaded;historyOpen.value=false;
    sessionStorage.setItem(activeSessionKey, id);
    const lastChatId=[...loaded].reverse().find(message=>message.role==='assistant')?.chat_id;
    if (lastChatId == null || chatStore.currentMemory.chatId !== lastChatId) chatStore.clearMemory(id);
    await scrollToBottom();
  } catch {
    if (current()) {
      historyFailed.value=true;
      error.value='Không tải được lịch sử. Hãy thử lại hội thoại trước khi gửi tiếp.';
    }
  } finally {
    if (current()) historyLoading.value=false;
  }
}

async function removeSession(id: string) {
  await api.deleteSession(id);
  if (id === sessionId.value) await newChat();
  await refreshSessions();
}

function releaseMessagePreviews(items: UiMessage[]) {
  for (const message of items) {
    if (message.image_url?.startsWith('blob:')) URL.revokeObjectURL(message.image_url);
  }
}

function clearFile() {
  uploadSerial++;
  uploadLoading.value=false;
  if (selectedPreview.value) URL.revokeObjectURL(selectedPreview.value);
  if (fileInput.value) fileInput.value.value = "";
  selectedFile.value = null;
  selectedPreview.value = "";
  uploadToken.value = "";
}

function sourceLines(value: string, citations?: Citation[]): string[] {
  return formatSourceText(value, citations).split("\n").filter(Boolean);
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
  const file = (event.target as HTMLInputElement).files?.[0];
  clearFile();
  if (!file) return;
  selectedFile.value = file;
  selectedPreview.value = URL.createObjectURL(file);
  const request=uploadSerial, actor=identity.value;
  uploadLoading.value=true;
  try {
    const uploaded = await api.uploadChatImage(file);
    if (request===uploadSerial && actor===identity.value) uploadToken.value = uploaded.image_token;
  } catch {
    if (request===uploadSerial && actor===identity.value) {
      clearFile();error.value='Không tải được ảnh đính kèm. Hãy thử lại.';
    }
  } finally {
    if (request===uploadSerial && actor===identity.value) uploadLoading.value=false;
  }
}

async function send(resume?:UiMessage) {
  const question = resume ? String(resume.resumePayload?.question || '') : input.value.trim();
  if (!question || busy.value || historyLoading.value || historyFailed.value || uploadLoading.value || !actionState.value.valid) return;
  if (resume && (!canResume(resume) || (input.value.trim() && input.value.trim() !== question))) return;
  const request=++contentSerial, actor=identity.value;
  const controller=new AbortController();activeController=controller;
  const current=()=>request===contentSerial && actor===identity.value && sessionId.value===currentSession;
  const receiving=()=>current() && !controller.signal.aborted;
  const actionPayload=actionState.value.active ? (actionState.value.text_query ? {text_query:true} :
    actionState.value.scope_total ? {scope_total:{...actionState.value.scope_total}} :
    actionState.value.scope_comparison ? {scope_comparison:{...actionState.value.scope_comparison}} :
    actionState.value.graph_query ? {graph_query:{...actionState.value.graph_query}} :
    {query_plan:JSON.parse(JSON.stringify(actionState.value.query_plan))}) : {};
  error.value = "";
  warning.value = "";
  busy.value = true;
  thinking.value = false;
  input.value = "";

  const currentSession = sessionId.value;
  if (!resume && messages.value.at(-1)?.progressState === "error" && messages.value.at(-1)?.search_progress?.search_mode !== 'comprehensive' && messages.value.at(-2)?.role === "user") {
    releaseMessagePreviews(messages.value.slice(-2));
    messages.value = messages.value.slice(0, -2);
  }
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
  const targetIndex=resume ? messages.value.indexOf(resume) : messages.value.length+1;
  if (!resume) messages.value=[...messages.value.map(message=>({...message,resumePayload:undefined})),userMessage, {
    role: "assistant",
    content: "",
    progress: ["Đã nhận câu hỏi"],
    progressState: "running",
  }];
  const imageToken = resume ? resume.resumePayload?.image_token : uploadToken.value || null;
  // The sent message owns this URL until it leaves the conversation.
  if (!resume) selectedPreview.value = "";
  clearFile();
  await scrollToBottom();

  const memory = chatStore.currentMemory;
  if (!current()) return;
  const payload=resume ? {...resume.resumePayload,search_mode:'comprehensive',
    continuation_cursor:resume.search_progress?.continuation_cursor} : {
    session_id:currentSession,question,image_token:imageToken,chat_history:history,
    current_part_ids:memory.currentPartIds,conversation_context:memory.conversationContext,
    search_mode:actionState.value.active ? 'quick' : searchMode.value,...actionPayload};
  messages.value=messages.value.map((message,index)=>index===targetIndex ? {
    ...message,resumePayload:payload,progressState:'running',progress:['Đã nhận câu hỏi'],
    search_progress:message.search_progress ?? {search_mode:payload.search_mode as SearchMode},
  } : message);
  let turnText='';
  const update=(fn:(message:UiMessage)=>UiMessage)=>{
    messages.value=messages.value.map((message,index)=>index===targetIndex ? fn(message) : message);
  };
  const progressReceived=(progress:SearchProgress)=>{
    if (!receiving()) return;
    update(message=>applyScanProgress(message,progress));
  };
  chatStore.pendingSessionIds=[...chatStore.pendingSessionIds,currentSession];
  try {
    await api.sendChatMessage(
      payload,
      {
        onProgress:progressReceived,
        onThinking() {
          if (!receiving()) return;
          thinking.value = true;
          setAssistantProgress("Đang phân tích ý định và tra cứu tài liệu");
          scrollToBottom();
        },
        onDelta(text) {
          if (!receiving()) return;
          turnText+=text;
          thinking.value = false;
          setAssistantProgress("Đang soạn câu trả lời từ tài liệu");
          update(message=>message.search_progress?.search_mode==='comprehensive' ? message : {...message,content:turnText});
          scrollToBottom();
        },
        onWarning(message) {
          if (!receiving()) return;
          warning.value = message;
        },
        onError(message,data) {
          if (!receiving()) return;
          error.value = message;
          setAssistantProgress("Có lỗi khi xử lý câu hỏi", "error");
          if (data?.search_progress) progressReceived(data.search_progress);
          if (data?.status===401 || data?.status===403) progressReceived({search_mode:'comprehensive',scan_status:'denied'});
          update(last=>({...last,content:last.search_progress?.search_mode==='comprehensive' ? last.content : `Lỗi: ${message}`,search_progress:last.search_progress ? {...last.search_progress,
            scan_status:['stale','denied','paused'].includes(last.search_progress.scan_status ?? '') ? last.search_progress.scan_status : 'error'} : undefined}));
          if (!imageToken && messages.value[targetIndex]?.search_progress?.search_mode!=='comprehensive') input.value = question;
        },
        onDone(data) {
          if (!receiving()) return;
          update(message=>({...mergeScanResult(message,data,turnText),progressState:'done',
            progress:['Đã kết thúc lượt trả lời']}));
          if (chatStore.memoryIdentity === actor) {
            if (data.chat_id == null) chatStore.clearMemory(currentSession);
            else chatStore.updateMemory(currentSession, {
              chatId: data.chat_id,
              currentPartIds: data.new_part_ids ?? [],
              conversationContext: data.conversation_context ?? null,
            });
          }
          if (!current()) return;
          thinking.value = false;
          if (data.chat_id != null) sessionStorage.setItem(activeSessionKey, currentSession);
          refreshSessions();
        },
      },
      controller.signal,
    );
  } catch (err) {
    if (!current()) return;
    if (controller.signal.aborted) return;
    error.value = err instanceof Error ? err.message : "Gửi câu hỏi thất bại";
    setAssistantProgress("Có lỗi khi gửi câu hỏi", "error");
    const status=(err as {status?:number})?.status;
    if (status===401 || status===403) progressReceived({search_mode:'comprehensive',scan_status:'denied'});
    update(last=>({...last,content:last.search_progress?.search_mode==='comprehensive' ? last.content : `Lỗi: ${error.value}`,
      search_progress:last.search_progress ? {...last.search_progress,scan_status:status===401 || status===403 ? 'denied' : 'error'} : undefined}));
    if (!imageToken && messages.value[targetIndex]?.search_progress?.search_mode!=='comprehensive') input.value = question;
  } finally {
    if (chatStore.memoryIdentity === actor) {
      chatStore.pendingSessionIds=chatStore.pendingSessionIds.filter(id=>id!==currentSession);
    }
    if (current()) {busy.value = false;thinking.value = false;activeController=undefined;}
  }
}

function canResume(message:UiMessage) {
  const p=message.search_progress;
  return !!message.resumePayload && !!p?.continuation_cursor && !['stale','denied'].includes(p.scan_status ?? '')
    && (p.results_pending || !p.snapshot_complete) && !(p.scan_status==='error' && p.pending_sources===0 && !p.results_pending);
}

async function stop() {
  activeController?.abort();
  messages.value=messages.value.map(message=>message.progressState==='running' ? {
    ...message,progressState:'done',progress:['Đã dừng lượt trả lời'],
    search_progress:message.search_progress ? {...message.search_progress,scan_status:'paused',turn_complete:false} : undefined,
  } : message);
  thinking.value=false;
  await nextTick();composerEl.value?.querySelector('textarea')?.focus();
}

async function restart(message:UiMessage) {
  if (busy.value || !message.resumePayload) return;
  input.value=String(message.resumePayload.question || '');searchMode.value='comprehensive';
  await nextTick();composerEl.value?.querySelector('textarea')?.focus();
}

function scanLabel(p:SearchProgress) {
  if (p.scan_status==='stale') return 'Nguồn đã thay đổi. Hãy bắt đầu lại lần rà.';
  if (p.scan_status==='denied') return 'Quyền đọc nguồn đã thay đổi. Kết quả cũ đã được ẩn.';
  if (p.search_mode==='quick') return 'Tra cứu nhanh trong các nguồn tìm được; chưa xác nhận đã rà toàn bộ.';
  if (p.results_pending) return p.snapshot_complete ? 'Đã rà xong phạm vi nguồn; còn kết quả đã lưu chưa nhận.' : 'Còn kết quả đã lưu chưa nhận.';
  if (p.snapshot_complete) return 'Đã rà xong toàn bộ phạm vi nguồn được phép của lần rà này.';
  if (p.scan_status==='paused') return 'Đã tạm dừng; lần rà chưa hoàn tất.';
  if (p.scan_status==='error') return 'Có lỗi xử lý; lần rà chưa hoàn tất.';
  return 'Lần rà chưa hoàn tất.';
}

function answerLabel(message:UiMessage) {
  if (message.progressState==='error') return 'Lỗi kỹ thuật khi xử lý câu hỏi.';
  const outcomes:Record<string,string>={full_answer:'Đã trả lời các ý có căn cứ.',partial_answer:'Chỉ trả lời được một phần.',
    clarification_required:'Cần làm rõ câu hỏi.',insufficient_evidence:'Chưa đủ bằng chứng trong phạm vi đã tra cứu.',refused:'Không thể trả lời yêu cầu này.'};
  const verification:Record<string,string>={verified:'Đã kiểm chứng các ý trả lời với nguồn.',partial:'Một phần câu trả lời đã được kiểm chứng.',
    insufficient:'Chưa đủ bằng chứng để xác nhận.',unverified:'Chưa xác nhận kiểm chứng nội dung.'};
  return [outcomes[message.answer_outcome ?? ''],verification[message.verification_status ?? ''] || 'Chưa xác nhận kiểm chứng nội dung.'].filter(Boolean).join(' ');
}

async function rate(message: ChatMessage, rating: number) {
  if (!message.chat_id) return;
  await api.sendFeedback(message.chat_id, rating);
  message.feedback = rating;
}

watch(()=>chatStore.pendingSessionIds, pending=>{
  if (waitingSession.value && !pending.includes(waitingSession.value)) openSession(waitingSession.value);
});
watch(identity,async(value,previous)=>{
  const restore=chatStore.memoryIdentity===value;
  catalogSerial++;sessions.value=[];input.value='';catalogError.value='';catalogLoading.value=false;
  chatStore.bindMemoryIdentity(value);
  resetView();
  const request=contentSerial;
  if (previous!==undefined) sessionStorage.removeItem(activeSessionKey);
  if (restore) openSession(sessionId.value);
  if (authStore.user || previous===undefined) {
    await refreshSessions();
    if (!restore && previous===undefined && request===contentSerial && value===identity.value) {
      const savedSession=sessionStorage.getItem(activeSessionKey);
      if (savedSession && sessions.value.some(session=>session.session_id===savedSession)) await openSession(savedSession);
      else sessionStorage.removeItem(activeSessionKey);
    }
  }
},{immediate:true});
onBeforeUnmount(()=>{catalogSerial++;resetView();});
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
          <Tag :value="busy ? 'Đang xử lý' : historyLoading ? 'Đang khôi phục hội thoại' : historyFailed ? 'Chưa tải được hội thoại' : 'Sẵn sàng'"
            :severity="busy || historyLoading || historyFailed ? 'warn' : 'success'" />
        </div>
      </header>

      <div :class="['chat-body', { 'with-history': historyOpen }]">
        <div class="chat-column">
          <div ref="scrollEl" class="message-scroll">
            <div v-if="messages.length === 0 && !historyLoading && !historyFailed" class="empty-state">
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
                <div v-if="message.role === 'assistant' && !message.content && message.progressState === 'running'" class="thinking-row">
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
                  v-html="renderMarkdown(message.content, message.citations)"
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
                  <div class="source-card">
                    <p v-for="line in sourceLines(message.ref_text, message.citations)" :key="line">{{ line }}</p>
                  </div>
                </details>
                <AdvancedRagResult v-if="message.advanced_rag" :result="message.advanced_rag"
                  @unavailable="invalidateSourceMessage(message)" />

                <p v-if="message.role === 'assistant' && message.progressState !== 'running'">{{ answerLabel(message) }}</p>
                <AnswerSources :message="message" @unavailable="invalidateSourceMessage(message)" />
                <section v-if="message.search_progress" class="scan-status" aria-label="Tiến độ rà nguồn">
                  <p role="status" aria-live="polite">{{ scanLabel(message.search_progress) }}</p>
                  <p v-if="message.search_progress.search_mode === 'comprehensive' && message.search_progress.total_sources !== undefined">
                    Rà toàn bộ phạm vi được phép: đã kiểm {{ message.search_progress.checked_sources ?? 0 }}/{{ message.search_progress.total_sources }} nguồn;
                    còn {{ message.search_progress.pending_sources ?? 0 }}, lỗi {{ message.search_progress.error_sources ?? 0 }}.
                  </p>
                  <Button v-if="canResume(message)" :label="message.search_progress.results_pending ? 'Xem tiếp kết quả đã lưu' : 'Tiếp tục rà'"
                    :disabled="busy || !!input.trim() && input.trim() !== message.resumePayload?.question" @click="send(message)" />
                  <Button v-if="message.resumePayload && message.search_progress.search_mode === 'comprehensive' && ['stale','denied','error'].includes(message.search_progress.scan_status || '')"
                    label="Bắt đầu lại lần rà" :disabled="busy" @click="restart(message)" />
                  <p v-if="message.search_progress.scan_status === 'paused' && !message.search_progress.continuation_cursor">Chưa nhận được điểm tiếp tục. Gửi lại câu hỏi để bắt đầu lần rà mới.</p>
                </section>

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

          <p v-if="historyLoading" role="status">{{ waitingSession ? 'Đang chờ câu trả lời trước hoàn tất để khôi phục hội thoại…' : 'Đang tải lịch sử…' }}</p>
          <p v-if="uploadLoading" role="status">Đang tải ảnh…</p>
          <Message v-if="catalogError" severity="error">{{ catalogError }}</Message>
          <Button v-if="catalogError" label="Thử lại danh sách lịch sử" :disabled="catalogLoading" @click="refreshSessions" />
          <Message v-if="warning" severity="warn">{{ warning }}</Message>
          <Message v-if="error" severity="error">{{ error }}</Message>
          <Button v-if="historyFailed" label="Thử lại hội thoại" @click="openSession(sessionId)" />

          <footer ref="composerEl" class="composer">
            <label for="search-mode">Cách tra cứu</label>
            <select id="search-mode" v-model="searchMode" :disabled="busy || actionState.active">
              <option value="quick">Tra cứu nhanh</option>
              <option value="comprehensive">Rà toàn bộ phạm vi được phép</option>
            </select>
            <p v-if="actionState.active">Tác vụ chuyên biệt dùng chế độ tra cứu nhanh.</p>
            <Button v-if="busy" label="Dừng trả lời" @click="stop" />
            <AdvancedRagActions :key="sessionId" :disabled="busy || historyLoading || historyFailed" @change="actionState=$event" />
            <div v-if="selectedPreview" class="selected-file">
              <img :src="selectedPreview" alt="Ảnh đã chọn" />
              <span>{{ selectedFile?.name }}</span>
              <Button label="Bỏ file" severity="secondary" text @click="clearFile" />
            </div>
            <div class="composer-row">
              <button type="button" class="file-button" :disabled="busy || historyLoading || historyFailed" @click="fileInput?.click()">Đính kèm</button>
              <input ref="fileInput" hidden type="file" accept=".png,.jpg,.jpeg,.bmp,.gif,.webp,.tif,.tiff" @change="onFileChange" />
              <Textarea
                v-model="input"
                auto-resize
                rows="1"
                aria-label="Câu hỏi"
                placeholder="Hỏi bất cứ điều gì"
                @keydown.enter.exact.prevent="send()"
              />
              <Button :label="busy ? 'Đang gửi' : 'Gửi'" :disabled="busy || historyLoading || historyFailed || uploadLoading || !input.trim() || !actionState.valid" @click="send()" />
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
.message-content :deep(.citation-marker) {
  display: inline-block;
  border: 1px solid rgba(56, 189, 248, 0.32);
  border-radius: 5px;
  background: rgba(56, 189, 248, 0.1);
  color: #bcecff;
  font-size: 0.82em;
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
.source-block {
  margin-top: 0.85rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(15, 23, 42, 0.42);
  padding: 0.7rem 0.85rem;
}
.source-block summary {
  cursor: pointer;
  color: var(--faint);
  font-size: 0.82rem;
  font-weight: 700;
}
.source-card {
  display: grid;
  gap: 0.25rem;
  margin-top: 0.55rem;
  color: var(--muted);
  font-size: 0.84rem;
  line-height: 1.5;
}
.source-card p {
  margin: 0;
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
.file-button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
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
.structured-citation { grid-column:1 / -1; }
</style>
