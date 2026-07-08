<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { pickNum } from "@/utils/rows";
import type { ApiRow, CreateForm, RowAction } from "@/types";

async function loadQuestions(): Promise<ApiRow[]> {
  const data = await apiGet<{ questions: ApiRow[] }>("/api/regression/questions", { active_only: false });
  return data.questions ?? [];
}

async function loadRuns(): Promise<ApiRow[]> {
  const data = await apiGet<{ runs: ApiRow[] }>("/api/regression/runs");
  return data.runs ?? [];
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
    <ResourcePage
      title="Bộ câu hỏi regression"
      eyebrow="Regression"
      description="Quản lý bộ câu hỏi kiểm thử chất lượng RAG."
      :load="loadQuestions"
      :create-form="createForm"
      :row-actions="rowActions"
    />
    <ResourcePage
      title="Lịch sử chạy"
      eyebrow="Regression"
      description="Kết quả các lần chạy regression gần đây."
      :load="loadRuns"
    />
  </div>
</template>
