<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { str } from "@/utils/rows";
import type { ApiRow, CreateForm, ResourceColumn, RowAction } from "@/types";

const columns: ResourceColumn[] = [
  { field: "key", header: "Khóa" },
  { field: "value", header: "Giá trị" },
];

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ settings: Record<string, unknown> }>("/api/settings");
  const settings = data.settings ?? {};
  return Object.entries(settings).map(([key, value]) => ({ key, value }));
}

const createForm: CreateForm = {
  title: "Đặt cấu hình",
  triggerLabel: "Đặt khóa",
  fields: [
    { key: "key", label: "Khóa", required: true },
    { key: "value", label: "Giá trị", required: true },
  ],
  submit: (values) =>
    apiSend(`/api/settings/${encodeURIComponent(String(values.key))}`, "PUT", { value: values.value }),
};

const rowActions: RowAction[] = [
  {
    label: "Sửa",
    run: async (r) => {
      const key = str(r, "key");
      const next = window.prompt(`Giá trị mới cho "${key}"`, str(r, "value"));
      if (next !== null) await apiSend(`/api/settings/${encodeURIComponent(key)}`, "PUT", { value: next });
    },
  },
];
</script>

<template>
  <ResourcePage
    title="Cấu hình"
    eyebrow="Settings"
    description="Cấu hình ứng dụng (AppSettings)."
    :columns="columns"
    :load="load"
    :create-form="createForm"
    :row-actions="rowActions"
  />
</template>
