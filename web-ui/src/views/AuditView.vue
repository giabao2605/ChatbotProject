<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet } from "@/api/client";
import type { ApiRow, ResourceColumn } from "@/types";

// GD4: backend nay tra dict co ten cot (_rows_to_json) thay vi mang vi tri.
const columns: ResourceColumn[] = [
  { field: "AuditID", header: "AuditID" },
  { field: "Username", header: "Người dùng" },
  { field: "Action", header: "Hành động", kind: "tag" },
  { field: "EntityType", header: "Loại đối tượng" },
  { field: "EntityID", header: "Đối tượng" },
  { field: "Details", header: "Chi tiết" },
  { field: "CreatedAt", header: "Thời gian" },
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
