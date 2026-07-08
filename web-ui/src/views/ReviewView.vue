<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { num } from "@/utils/rows";
import type { ApiRow, RowAction } from "@/types";

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ documents: ApiRow[] }>("/api/documents/pending-review");
  return data.documents ?? [];
}

function docId(row: ApiRow): number {
  // pending-review rows are dicts; find a DocID-like field.
  const keys = Object.keys(row);
  const hit = keys.find((k) => k.toLowerCase() === "docid") || keys.find((k) => k.toLowerCase().includes("docid"));
  return Number(hit ? row[hit] : num(row, "DocID"));
}

const rowActions: RowAction[] = [
  {
    label: "Xuất bản (version)",
    run: (r) => apiSend(`/api/documents/${docId(r)}/publish-new-version`, "POST"),
  },
  {
    label: "Xuất bản (variant)",
    run: (r) => apiSend(`/api/documents/${docId(r)}/publish-new-variant`, "POST"),
  },
  {
    label: "Xuất bản (độc lập)",
    run: (r) => apiSend(`/api/documents/${docId(r)}/publish-standalone`, "POST"),
  },
  {
    label: "Từ chối",
    severity: "warning",
    confirm: "Từ chối tài liệu này?",
    run: (r) => apiSend(`/api/documents/${docId(r)}/reject`, "POST"),
  },
  {
    label: "Lưu trữ",
    run: (r) => apiSend(`/api/documents/${docId(r)}/archive`, "POST"),
  },
  {
    label: "Xoá",
    severity: "danger",
    confirm: "Xoá vĩnh viễn?",
    run: (r) => apiSend(`/api/documents/${docId(r)}`, "DELETE"),
  },
];
</script>

<template>
  <ResourcePage
    title="Duyệt tài liệu"
    eyebrow="Review"
    description="Tài liệu đang chờ duyệt. Chọn hình thức xuất bản hoặc từ chối."
    :load="load"
    :row-actions="rowActions"
  />
</template>
