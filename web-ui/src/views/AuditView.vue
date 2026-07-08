<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet } from "@/api/client";
import type { ApiRow, ResourceColumn } from "@/types";

// Positional columns from list_recent_audit (see ui_queries.py).
const columns: ResourceColumn[] = [
  { field: "c0", header: "AuditID" },
  { field: "c1", header: "Người dùng" },
  { field: "c2", header: "Hành động", kind: "tag" },
  { field: "c3", header: "Loại đối tượng" },
  { field: "c4", header: "Đối tượng" },
  { field: "c5", header: "Chi tiết" },
  { field: "c6", header: "Thời gian" },
];

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ logs: ApiRow[] }>("/api/audit", { limit: 200 });
  return data.logs ?? [];
}
</script>

<template>
  <ResourcePage
    title="Audit Log"
    eyebrow="Security"
    description="Nhật ký hành động gần đây trong hệ thống."
    :columns="columns"
    :load="load"
  />
</template>
