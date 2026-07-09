<script setup lang="ts">
import { ref } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { pickNum } from "@/utils/rows";
import type { ApiRow, CreateForm, RowAction } from "@/types";

type RunSummary = { batch_id: string; total: number; passed: number; failed: number; pass_rate: number };

const runsRef = ref<InstanceType<typeof ResourcePage>>();
const running = ref(false);
const summaryText = ref("");
const runError = ref("");

async function loadQuestions(): Promise<ApiRow[]> {
  const data = await apiGet<{ questions: ApiRow[] }>("/api/regression/questions", { active_only: false });
  return data.questions ?? [];
}

async function loadRuns(): Promise<ApiRow[]> {
  const data = await apiGet<{ runs: ApiRow[] }>("/api/regression/runs");
  return data.runs ?? [];
}

// GD7.3: chay bo cau hoi regression tu UI (endpoint POST /api/regression/run).
async function runRegression() {
  running.value = true;
  runError.value = "";
  summaryText.value = "";
  try {
    const res = await apiSend<{ ok: boolean; summary: RunSummary }>("/api/regression/run", "POST", {});
    const s = res.summary;
    summaryText.value = `Batch ${s.batch_id}: ${s.passed}/${s.total} đạt (tỉ lệ ${Math.round(
      (s.pass_rate ?? 0) * 100,
    )}%), ${s.failed} lỗi.`;
    runsRef.value?.refresh();
  } catch (e) {
    runError.value = e instanceof Error ? e.message : "Lỗi chạy regression";
  } finally {
    running.value = false;
  }
}

const createForm: CreateForm = {
  title: "Thêm câu hỏi regression",
  fields: [
    { key: "question", label: "Câu hỏi", required: true, type: "textarea" },
    { key: "expected_doc_id", label: "DocID kỳ vọng", type: "number" },
    { key: "expected_keywords", label: "Từ khóa kỳ vọng (cách nhau dấu ,)" },
    { key: "department", label: "Phòng ban" },
    { key: "site", label: "Site" },
  ],
  submit: (values) =>
    apiSend("/api/regression/questions", "POST", {
      ...values,
      expected_doc_id: values.expected_doc_id ? Number(values.expected_doc_id) : null,
    }),
};

function qid(row: ApiRow): number {
  return pickNum(row, ["RegQID", "QuestionID", "id"]);
}

const rowActions: RowAction[] = [
  { label: "Kích hoạt", run: (r) => apiSend(`/api/regression/questions/${qid(r)}/active`, "PATCH", { is_active: true }) },
  { label: "Vô hiệu", severity: "warning", run: (r) => apiSend(`/api/regression/questions/${qid(r)}/active`, "PATCH", { is_active: false }) },
];
</script>

<template>
  <div class="stacked-pages">
    <div class="run-bar">
      <div class="run-bar-info">
        <strong>Chạy kiểm thử hồi quy</strong>
        <span class="muted-text">Chạy toàn bộ câu hỏi đang kích hoạt qua engine RAG (có thể mất vài phút).</span>
      </div>
      <Button :label="running ? 'Đang chạy…' : 'Chạy regression'" :loading="running" @click="runRegression" />
    </div>
    <Message v-if="summaryText" severity="success" v-text="summaryText"></Message>
    <Message v-if="runError" severity="error" v-text="runError"></Message>

    <ResourcePage
      title="Bộ câu hỏi regression"
      eyebrow="Regression"
      description="Quản lý bộ câu hỏi kiểm thử chất lượng RAG."
      :load="loadQuestions"
      :create-form="createForm"
      :row-actions="rowActions"
    />
    <ResourcePage
      ref="runsRef"
      title="Lịch sử chạy"
      eyebrow="Regression"
      description="Kết quả các lần chạy regression gần đây."
      :load="loadRuns"
    />
  </div>
</template>

<style scoped>
.run-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.9rem 1rem;
  border: 1px solid var(--p-surface-200, #e5e7eb);
  border-radius: 10px;
  background: var(--p-surface-50, #f8fafc);
}
.run-bar-info {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.muted-text {
  color: #64748b;
  font-size: 0.85rem;
}
</style>
