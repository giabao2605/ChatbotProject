<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { pickStr } from "@/utils/rows";
import type { ApiRow, CreateForm, RowAction } from "@/types";

async function loadDepartments(): Promise<ApiRow[]> {
  const data = await apiGet<{ departments: ApiRow[] }>("/api/catalog/departments", { active_only: false });
  return data.departments ?? [];
}

async function loadSites(): Promise<ApiRow[]> {
  const data = await apiGet<{ sites: ApiRow[] }>("/api/catalog/sites", { active_only: false });
  return data.sites ?? [];
}

const deptForm: CreateForm = {
  title: "Thêm phòng ban",
  fields: [
    { key: "code", label: "Mã phòng ban", required: true },
    { key: "name", label: "Tên", required: true },
    { key: "domain", label: "Domain" },
    { key: "site", label: "Site" },
  ],
  submit: (values) => apiSend("/api/catalog/departments", "POST", values),
};

const siteForm: CreateForm = {
  title: "Thêm site",
  fields: [
    { key: "code", label: "Mã site", required: true },
    { key: "name", label: "Tên", required: true },
  ],
  submit: (values) => apiSend("/api/catalog/sites", "POST", values),
};

function deptCode(row: ApiRow): string {
  return pickStr(row, ["code", "MaPhong", "DepartmentCode", "department"]);
}

const deptActions: RowAction[] = [
  {
    label: "Đổi trạng thái",
    run: async (r) => {
      const status = window.prompt("Trạng thái mới (active/inactive)", "active");
      if (status) await apiSend(`/api/catalog/departments/${deptCode(r)}/status`, "PATCH", { is_active: status === "active" });
    },
  },
  {
    label: "Lưu trữ",
    severity: "warning",
    confirm: "Lưu trữ phòng ban này?",
    run: (r) => apiSend(`/api/catalog/departments/${deptCode(r)}/archive`, "POST", { force: false }),
  },
];
</script>

<template>
  <div class="stacked-pages">
    <ResourcePage
      title="Phòng ban"
      eyebrow="Catalog"
      description="Danh mục phòng ban và trạng thái."
      :load="loadDepartments"
      :create-form="deptForm"
      :row-actions="deptActions"
    />
    <ResourcePage
      title="Site"
      eyebrow="Catalog"
      description="Danh mục site / nhà máy."
      :load="loadSites"
      :create-form="siteForm"
    />
  </div>
</template>
