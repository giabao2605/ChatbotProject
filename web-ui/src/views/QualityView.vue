<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import type { ApiRow, ResourceFilter, ToolbarAction } from "@/types";

const filters: ResourceFilter[] = [{ key: "worst_first", label: "Kém nhất trước", type: "checkbox", value: true }];

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
    description="Điểm chất lượng trích xuất theo tài liệu."
    :filters="filters"
    :load="load"
    :toolbar="toolbar"
  />
</template>
