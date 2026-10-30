<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import type { ApiRow, ResourceColumn, ResourceFilter, ToolbarAction } from "@/types";

const filters: ResourceFilter[] = [{ key: "worst_first", label: "Kém nhất trước", type: "checkbox", value: true }];
const columns: ResourceColumn[] = [
  { field: "doc_id", header: "Tài liệu" },
  { field: "file", header: "Tệp" },
  { field: "version_no", header: "Phiên bản" },
  { field: "is_current", header: "Bản hiện hành" },
  { field: "lifecycle_status", header: "Vòng đời", kind: "tag" },
  { field: "like", header: "Thích" },
  { field: "dislike", header: "Không thích" },
  { field: "wl", header: "Thích có trọng số" },
  { field: "wd", header: "Không thích có trọng số" },
  { field: "quality", header: "Điểm phản hồi", kind: "score" },
  { field: "net", header: "Chênh lệch trọng số" },
  { field: "n", header: "Số phản hồi" },
  { field: "computed_at", header: "Thời điểm tính" },
  { field: "reliable", header: "Đủ mẫu đánh giá", kind: "bool" },
];



async function load(f: Record<string, unknown>): Promise<ApiRow[]> {
  const data = await apiGet<{ documents: ApiRow[] }>("/api/quality/documents", { limit: 100, ...f });
  return data.documents ?? [];
}

const toolbar: ToolbarAction[] = [
  { label: "Tính lại điểm", confirm: "Tính lại điểm chất lượng?", run: () => apiSend("/api/quality/recompute", "POST") },
];
</script>

<template>
  <ResourcePage
    title="Chất lượng tài liệu"
    eyebrow="Quality"
    description="DocQualityScore được tính từ phản hồi người dùng; đây không phải điểm extraction hay classifier."
    :filters="filters"
    :columns="columns"
    :load="load"
    :toolbar="toolbar"
  />
</template>
