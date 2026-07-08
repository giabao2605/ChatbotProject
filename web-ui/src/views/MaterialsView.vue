<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { pickNum } from "@/utils/rows";
import type { ApiRow, CreateForm, RowAction } from "@/types";

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ materials: ApiRow[] }>("/api/materials");
  return data.materials ?? [];
}

const createForm: CreateForm = {
  title: "Thêm vật tư",
  fields: [
    { key: "code", label: "Mã vật tư", required: true },
    { key: "display", label: "Tên hiển thị", required: true },
    { key: "category", label: "Nhóm" },
  ],
  submit: (values) => apiSend("/api/materials", "POST", values),
};

function mid(row: ApiRow): number {
  return pickNum(row, ["MaterialID", "id"]);
}

const rowActions: RowAction[] = [
  {
    label: "Thêm đồng nghĩa",
    run: async (r) => {
      const syn = window.prompt("Từ đồng nghĩa mới", "");
      if (syn) await apiSend(`/api/materials/${mid(r)}/synonyms`, "POST", { synonym: syn });
    },
  },
  { label: "Xoá", severity: "danger", confirm: "Xoá vật tư?", run: (r) => apiSend(`/api/materials/${mid(r)}`, "DELETE") },
];
</script>

<template>
  <ResourcePage
    title="Từ điển vật tư"
    eyebrow="Materials"
    description="Danh mục vật tư và từ đồng nghĩa."
    :load="load"
    :create-form="createForm"
    :row-actions="rowActions"
  />
</template>
