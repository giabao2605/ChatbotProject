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
  { field: "lifecycle_status", header: "Vòng đời" },
  { field: "like", header: "Thích" },
  { field: "dislike", header: "Không thích" },
  { field: "wl", header: "Thích có trọng số" },
  { field: "wd", header: "Không thích có trọng số" },
  { field: "quality", header: "Điểm phản hồi" },
  { field: "net", header: "Chênh lệch trọng số" },
  { field: "n", header: "Số phản hồi" },
  { field: "computed_at", header: "Thời điểm tính" },
  { field: "reliable", header: "Đủ mẫu đánh giá" },
];

async function load(f: Record<string, unknown>): Promise<ApiRow[]> {
  const data = await apiGet<{ documents: ApiRow[] }>("/api/quality/documents", { limit: 100, ...f });
  return data.documents ?? [];
}

const toolbar: ToolbarAction[] = [
  { label: "Tính lại điểm", confirm: "Tính lại điểm chất lượng?", run: () => apiSend("/api/quality/recompute", "POST") },
  { label: "Dọn dẹp", severity: "warning", confirm: "Dọn dẹp dữ liệu chất lượng cũ?", run: () => apiSend("/api/quality/cleanup", "POST") },
];
</script>

<template>
  <ResourcePage
    title="Chất lượng tài liệu"
    eyebrow="Quality"
    description="Điểm chất lượng theo phản hồi người dùng về tài liệu."
    :filters="filters"
    :columns="columns"
    :load="load"
    :toolbar="toolbar"
  />
</template>
