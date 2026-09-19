<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import type { ApiRow, ResourceFilter, ToolbarAction } from "@/types";

const filters: ResourceFilter[] = [{ key: "worst_first", label: "Kém nhất trước", type: "checkbox", value: true }];

const columns = [
  { field: "doc_id", header: "Tài liệu" },
  { field: "file", header: "Tệp" },
  { field: "version_no", header: "Version" },
  { field: "lifecycle_status", header: "Vòng đời", kind: "tag" as const },
  { field: "quality", header: "Điểm feedback", kind: "score" as const },
  { field: "like", header: "Hữu ích" },
  { field: "dislike", header: "Không hữu ích" },
  { field: "n", header: "Số mẫu" },
  { field: "reliable", header: "Đủ mẫu", kind: "bool" as const },
  { field: "computed_at", header: "Tính lúc" },
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
    description="DocQualityScore được tính từ feedback người dùng; đây không phải điểm extraction hay classifier."
    :filters="filters"
    :load="load"
    :columns="columns"
    :toolbar="toolbar"
  />
</template>
