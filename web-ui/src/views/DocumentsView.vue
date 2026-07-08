<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet } from "@/api/client";
import { num } from "@/utils/rows";
import { SECURITY_LEVELS } from "@/utils/rows";
import type { ApiRow, ResourceColumn, ResourceFilter, RowAction } from "@/types";

const columns: ResourceColumn[] = [
  { field: "DocID", header: "DocID" },
  { field: "OriginalFileName", header: "Tên file" },
  { field: "Department", header: "Phòng ban" },
  { field: "Domain", header: "Domain" },
  { field: "SecurityLevel", header: "Mức mật", kind: "tag" },
  { field: "Site", header: "Site" },
  { field: "VersionNo", header: "Phiên bản" },
  { field: "IsCurrentVersion", header: "Hiện hành", kind: "bool" },
  { field: "LifecycleStatus", header: "Vòng đời", kind: "tag" },
  { field: "ReviewStatus", header: "Duyệt", kind: "tag" },
  { field: "CreatedAt", header: "Ngày tải" },
];

const filters: ResourceFilter[] = [
  { key: "search", label: "Tìm kiếm", type: "text" },
  { key: "dept", label: "Phòng ban", type: "text" },
  { key: "domain", label: "Domain", type: "text" },
  { key: "sec", label: "Mức mật", type: "select", options: SECURITY_LEVELS },
];

async function load(f: Record<string, unknown>): Promise<ApiRow[]> {
  const data = await apiGet<{ documents: ApiRow[] }>("/api/documents", f);
  return data.documents ?? [];
}

const rowActions: RowAction[] = [
  {
    label: "Xem trang",
    run: async (row) => {
      window.open(`/api/files/documents/${num(row, "DocID")}/pages/1`, "_blank");
    },
  },
  {
    label: "Tải bản gốc",
    run: async (row) => {
      window.open(`/api/files/documents/${num(row, "DocID")}/original`, "_blank");
    },
  },
];
</script>

<template>
  <ResourcePage
    title="Kho tài liệu"
    eyebrow="Documents"
    description="Danh sách tài liệu đã xuất bản theo quyền truy cập của bạn."
    :columns="columns"
    :filters="filters"
    :load="load"
    :row-actions="rowActions"
  />
</template>
