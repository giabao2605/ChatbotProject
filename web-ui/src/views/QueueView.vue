<script setup lang="ts">
import { onMounted, ref } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { num } from "@/utils/rows";
import type { ApiRow, ResourceColumn, ResourceFilter, RowAction } from "@/types";

// Positional columns from list_ingestion_jobs (see ui_queries.py).
const columns: ResourceColumn[] = [
  { field: "c0", header: "JobID" },
  { field: "c1", header: "Tên file" },
  { field: "c2", header: "Phòng ban" },
  { field: "c3", header: "Trạng thái", kind: "tag" },
  { field: "c11", header: "Tiến độ (%)" },
  { field: "c18", header: "Ưu tiên" },
  { field: "c6", header: "Người tải" },
  { field: "c5", header: "Tạo lúc" },
  { field: "c4", header: "Lỗi" },
];

const filters: ResourceFilter[] = [{ key: "status_value", label: "Lọc trạng thái", type: "text" }];

const eta = ref<number | null>(null);

async function load(f: Record<string, unknown>): Promise<ApiRow[]> {
  const data = await apiGet<{ jobs: ApiRow[] }>("/api/ingestion/jobs", f);
  return data.jobs ?? [];
}

async function loadEta() {
  try {
    const data = await apiGet<{ eta_seconds: number }>("/api/ingestion/eta");
    eta.value = data.eta_seconds ?? null;
  } catch {
    eta.value = null;
  }
}
onMounted(loadEta);

const rowActions: RowAction[] = [
  { label: "Huỷ", severity: "warning", run: (r) => apiSend(`/api/ingestion/jobs/${num(r, "c0")}/cancel`, "POST") },
  { label: "Xếp lại", run: (r) => apiSend(`/api/ingestion/jobs/${num(r, "c0")}/requeue`, "POST") },
  {
    label: "Ưu tiên cao",
    run: (r) => apiSend(`/api/ingestion/jobs/${num(r, "c0")}/priority`, "PATCH", { priority: 1 }),
  },
  { label: "Chờ duyệt", run: (r) => apiSend(`/api/ingestion/jobs/${num(r, "c0")}/pending-review`, "POST") },
  {
    label: "Xoá",
    severity: "danger",
    confirm: "Xoá job này?",
    run: (r) => apiSend(`/api/ingestion/jobs/${num(r, "c0")}`, "DELETE"),
  },
];
</script>

<template>
  <div>
    <ResourcePage
      title="Tiến trình ingest"
      eyebrow="Ingestion"
      :description="eta != null ? `Thời gian xử lý hàng đợi ước tính: ${eta}s` : 'Hàng đợi ingest hiện tại.'"
      :columns="columns"
      :filters="filters"
      :load="load"
      :row-actions="rowActions"
    />
  </div>
</template>
