<script setup lang="ts">
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

async function load(f: Record<string, unknown>): Promise<ApiRow[]> {
  const data = await apiGet<{ feedbacks: ApiRow[] }>("/api/feedback", f);
  return data.feedbacks ?? [];
}

function fid(row: ApiRow): number {
  return pickNum(row, ["FeedbackID", "id"]);
}

const rowActions: RowAction[] = [
  {
    label: "Phân loại",
    run: async (r) => {
      const failureType = window.prompt("Loại lỗi (ví dụ: wrong_answer, missing_doc)", "");
      if (!failureType) return;
      const correct = window.prompt("Đáp án đúng (tùy chọn)", "") ?? "";
      await apiSend(`/api/feedback/${fid(r)}/classify`, "POST", {
        failure_type: failureType,
        correct_answer: correct || null,
      });
    },
  },
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
</template>
