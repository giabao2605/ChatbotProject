<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { pickNum } from "@/utils/rows";
import type { ApiRow, CreateForm, ResourceColumn, RowAction } from "@/types";

// Backend tra ve cac dict key snake_case (xem list_domain_glossary).
const columns: ResourceColumn[] = [
  { field: "glossary_id", header: "ID" },
  { field: "term", header: "Thuật ngữ" },
  { field: "domain", header: "Domain" },
  { field: "synonyms", header: "Đồng nghĩa" },
  { field: "expansion", header: "Diễn giải" },
  { field: "is_active", header: "Hiệu lực", kind: "bool" },
  { field: "created_at", header: "Tạo lúc" },
];

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ terms: ApiRow[] }>("/api/glossary", { active_only: false });
  return data.terms ?? [];
}

const createForm: CreateForm = {
  title: "Thêm thuật ngữ",
  fields: [
    { key: "term", label: "Thuật ngữ", required: true },
    // Backend yêu cầu BẮT BUỘC có domain, nếu thiếu sẽ bỏ qua (không lưu).
    { key: "domain", label: "Domain", required: true },
    { key: "synonyms", label: "Đồng nghĩa (cách nhau dấu ,)" },
    { key: "expansion", label: "Diễn giải", type: "textarea" },
  ],
  submit: (values) => {
    // synonyms nhập dạng chuỗi "a, b" -> tách thành mảng để backend lưu đúng.
    const raw = values.synonyms;
    const synonyms =
      typeof raw === "string"
        ? raw.split(",").map((s) => s.trim()).filter(Boolean)
        : Array.isArray(raw)
          ? raw
          : [];
    return apiSend("/api/glossary", "POST", { ...values, synonyms });
  },
};

function gid(row: ApiRow): number {
  return pickNum(row, ["glossary_id", "GlossaryID", "id"]);
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
    :columns="columns"
    :load="load"
    :create-form="createForm"
    :row-actions="rowActions"
  />
</template>
