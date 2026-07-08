<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { pickNum } from "@/utils/rows";
import type { ApiRow, CreateForm, RowAction } from "@/types";

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ terms: ApiRow[] }>("/api/glossary", { active_only: false });
  return data.terms ?? [];
}

const createForm: CreateForm = {
  title: "Thêm thuật ngữ",
  fields: [
    { key: "term", label: "Thuật ngữ", required: true },
    { key: "domain", label: "Domain" },
    { key: "synonyms", label: "Đồng nghĩa (cách nhau dấu ,)" },
    { key: "expansion", label: "Diễn giải", type: "textarea" },
  ],
  submit: (values) => apiSend("/api/glossary", "POST", values),
};

function gid(row: ApiRow): number {
  return pickNum(row, ["GlossaryID", "id"]);
}

const rowActions: RowAction[] = [
  { label: "Kích hoạt", run: (r) => apiSend(`/api/glossary/${gid(r)}/active`, "PATCH", { is_active: true }) },
  { label: "Vô hiệu", severity: "warning", run: (r) => apiSend(`/api/glossary/${gid(r)}/active`, "PATCH", { is_active: false }) },
  { label: "Xoá", severity: "danger", confirm: "Xoá thuật ngữ?", run: (r) => apiSend(`/api/glossary/${gid(r)}`, "DELETE") },
];
</script>

<template>
  <ResourcePage
    title="Từ điển đồng nghĩa"
    eyebrow="Glossary"
    description="Quản lý thuật ngữ và từ đồng nghĩa dùng cho truy vấn."
    :load="load"
    :create-form="createForm"
    :row-actions="rowActions"
  />
</template>
