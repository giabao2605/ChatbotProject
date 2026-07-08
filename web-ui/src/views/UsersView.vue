<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiSend, apiGet } from "@/api/client";
import { SECURITY_LEVELS } from "@/utils/rows";
import type { ApiRow, CreateForm, RowAction } from "@/types";

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ users: ApiRow[] }>("/api/users");
  return data.users ?? [];
}

function userId(row: ApiRow): number {
  const keys = Object.keys(row);
  const hit =
    keys.find((k) => k.toLowerCase() === "userid") ||
    keys.find((k) => k.toLowerCase() === "id") ||
    keys.find((k) => k.toLowerCase().includes("userid"));
  return Number(hit ? row[hit] : NaN);
}

function csv(value: string | null): string[] {
  return (value ?? "")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

const createForm: CreateForm = {
  title: "Tạo người dùng",
  fields: [
    { key: "username", label: "Tên đăng nhập", required: true },
    { key: "password", label: "Mật khẩu (≥ 8 ký tự)", required: true },
    { key: "display_name", label: "Tên hiển thị" },
    { key: "department", label: "Phòng ban chính" },
    { key: "roles", label: "Vai trò (cách nhau dấu ,)", placeholder: "user, uploader" },
    { key: "departments", label: "Phòng ban (cách nhau dấu ,)" },
  ],
  submit: (values) =>
    apiSend("/api/users", "POST", {
      username: values.username,
      password: values.password,
      display_name: values.display_name || null,
      department: values.department || null,
      roles: csv(values.roles as string),
      departments: csv(values.departments as string),
    }),
};

const rowActions: RowAction[] = [
  { label: "Kích hoạt", run: (r) => apiSend(`/api/users/${userId(r)}/active`, "PATCH", { is_active: true }) },
  {
    label: "Vô hiệu",
    severity: "warning",
    run: (r) => apiSend(`/api/users/${userId(r)}/active`, "PATCH", { is_active: false }),
  },
  {
    label: "Đổi vai trò",
    run: async (r) => {
      const add = window.prompt("Thêm vai trò (cách nhau dấu ,)", "") ?? "";
      const del = window.prompt("Gỡ vai trò (cách nhau dấu ,)", "") ?? "";
      await apiSend(`/api/users/${userId(r)}/roles`, "PATCH", {
        is_active: true,
        add_roles: csv(add),
        del_roles: csv(del),
      });
    },
  },
  {
    label: "Phòng ban",
    run: async (r) => {
      const depts = window.prompt("Danh sách phòng ban (cách nhau dấu ,)", "") ?? "";
      await apiSend(`/api/users/${userId(r)}/departments`, "PATCH", { departments: csv(depts) });
    },
  },
  {
    label: "Site",
    run: async (r) => {
      const sites = window.prompt("Danh sách site (cách nhau dấu ,)", "") ?? "";
      await apiSend(`/api/users/${userId(r)}/sites`, "PATCH", { sites: csv(sites) });
    },
  },
  {
    label: "Mức mật",
    run: async (r) => {
      const level = window.prompt(`Mức mật mới (${SECURITY_LEVELS.map((s) => s.value).join("/")})`, "public");
      if (level) await apiSend(`/api/users/${userId(r)}/clearance`, "PATCH", { max_level: level });
    },
  },
  {
    label: "Đổi mật khẩu",
    run: async (r) => {
      const pw = window.prompt("Mật khẩu mới (≥ 8 ký tự)", "");
      if (pw) await apiSend(`/api/users/${userId(r)}/password`, "PATCH", { password: pw });
    },
  },
  {
    label: "Xoá",
    severity: "danger",
    confirm: "Xoá người dùng này?",
    run: (r) => apiSend(`/api/users/${userId(r)}`, "DELETE"),
  },
];
</script>

<template>
  <ResourcePage
    title="Người dùng"
    eyebrow="Users"
    description="Quản lý tài khoản, vai trò, phòng ban, site và mức mật."
    :load="load"
    :create-form="createForm"
    :row-actions="rowActions"
  />
</template>
