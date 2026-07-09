<script setup lang="ts">
import { reactive } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { pickNum } from "@/utils/rows";
import type { ApiRow, ResourceColumn, ResourceFilter, RowAction } from "@/types";

const columns: ResourceColumn[] = [
  { field: "FeedbackID", header: "ID" },
  { field: "Question", header: "Câu hỏi" },
  { field: "BotAnswer", header: "Trả lời bot" },
  { field: "FailureType", header: "Loại lỗi", kind: "tag" },
  { field: "CorrectAnswer", header: "Đáp án đúng" },
  { field: "AddedToGoldenSet", header: "Golden", kind: "bool" },
  { field: "Department", header: "Phòng ban" },
  { field: "IsStale", header: "Cũ", kind: "bool" },
  { field: "CreatedAt", header: "Thời gian" },
];

const filters: ResourceFilter[] = [{ key: "only_pending", label: "Chỉ chưa phân loại", type: "checkbox" }];

// Cac loai loi thuong gap (goi y qua datalist; van cho phep go tu do).
const FAILURE_TYPES = [
  "wrong_answer",
  "missing_doc",
  "outdated_doc",
  "incomplete_answer",
  "hallucination",
  "retrieval_miss",
  "other",
];

async function load(f: Record<string, unknown>): Promise<ApiRow[]> {
  const data = await apiGet<{ feedbacks: ApiRow[] }>("/api/feedback", f);
  return data.feedbacks ?? [];
}

function fid(row: ApiRow): number {
  return pickNum(row, ["FeedbackID", "id"]);
}

// ---- Hop thoai phan loai (thay window.prompt) ----
const dlg = reactive({
  visible: false,
  busy: false,
  error: "",
  feedbackId: 0,
  question: "",
  failure_type: "",
  correct_answer: "",
  reviewer_note: "",
});
let resolveDlg: (() => void) | null = null;
let rejectDlg: ((e: unknown) => void) | null = null;

function openClassify(row: ApiRow): Promise<void> {
  return new Promise((resolve, reject) => {
    resolveDlg = resolve;
    rejectDlg = reject;
    dlg.feedbackId = fid(row);
    dlg.question = String(row.Question ?? "");
    dlg.failure_type = String(row.FailureType ?? "");
    dlg.correct_answer = String(row.CorrectAnswer ?? "");
    dlg.reviewer_note = "";
    dlg.error = "";
    dlg.visible = true;
  });
}

async function submit() {
  if (!dlg.failure_type.trim()) {
    dlg.error = "Chọn hoặc nhập loại lỗi.";
    return;
  }
  dlg.busy = true;
  dlg.error = "";
  try {
    await apiSend(`/api/feedback/${dlg.feedbackId}/classify`, "POST", {
      failure_type: dlg.failure_type.trim(),
      correct_answer: dlg.correct_answer.trim() || null,
      reviewer_note: dlg.reviewer_note.trim() || null,
    });
    dlg.visible = false;
    resolveDlg?.();
    resolveDlg = null;
    rejectDlg = null;
  } catch (err) {
    dlg.error = err instanceof Error ? err.message : "Lỗi";
  } finally {
    dlg.busy = false;
  }
}

function cancel() {
  dlg.visible = false;
  rejectDlg?.(new Error(""));
  resolveDlg = null;
  rejectDlg = null;
}

const rowActions: RowAction[] = [
  { label: "Phân loại", run: (r) => openClassify(r) },
  { label: "Xoá", severity: "danger", confirm: "Xoá feedback?", run: (r) => apiSend(`/api/feedback/${fid(r)}`, "DELETE") },
];
</script>

<template>
  <ResourcePage
    title="Feedback"
    eyebrow="Quality"
    description="Phản hồi người dùng; phân loại lỗi để đưa vào golden set."
    :columns="columns"
    :filters="filters"
    :load="load"
    :row-actions="rowActions"
  />

  <Dialog v-model:visible="dlg.visible" header="Phân loại feedback" modal :style="{ width: '520px' }" @hide="cancel">
    <div class="stack-form">
      <Message v-if="dlg.error" severity="error" v-text="dlg.error"></Message>
      <p v-if="dlg.question" class="muted-text" v-text="dlg.question"></p>
      <label class="field">
        <span>Loại lỗi *</span>
        <input v-model="dlg.failure_type" list="failureTypeList" class="native-input" placeholder="ví dụ: wrong_answer" />
        <datalist id="failureTypeList">
          <option v-for="ft in FAILURE_TYPES" :key="ft" :value="ft"></option>
        </datalist>
      </label>
      <label class="field">
        <span>Đáp án đúng (tùy chọn)</span>
        <textarea v-model="dlg.correct_answer" rows="3" class="native-input"></textarea>
      </label>
      <label class="field">
        <span>Ghi chú người duyệt (tùy chọn)</span>
        <textarea v-model="dlg.reviewer_note" rows="2" class="native-input"></textarea>
      </label>
    </div>
    <template #footer>
      <Button label="Huỷ" severity="secondary" outlined :disabled="dlg.busy" @click="cancel" />
      <Button label="Lưu" :loading="dlg.busy" @click="submit" />
    </template>
  </Dialog>
</template>

<style scoped>
.stack-form {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.field > span {
  font-weight: 600;
  font-size: 0.85rem;
}
.native-input {
  padding: 0.45rem 0.55rem;
  border: 1px solid var(--p-inputtext-border-color, #cbd5e1);
  border-radius: 6px;
  background: var(--p-inputtext-background, #fff);
  font: inherit;
  width: 100%;
  box-sizing: border-box;
}
.muted-text {
  color: #64748b;
  font-size: 0.85rem;
  font-style: italic;
}
</style>
