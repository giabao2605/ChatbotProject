<script setup lang="ts">
import { computed } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { num } from "@/utils/rows";
import { SECURITY_LEVELS } from "@/utils/rows";
import { useAuthStore } from "@/stores/auth";
import type { ApiRow, CreateForm, RowAction } from "@/types";

const auth = useAuthStore();
const canReview = computed(
  () => auth.user?.roles.includes("admin") || auth.user?.roles.includes("reviewer"),
);

async function loadMine(): Promise<ApiRow[]> {
  const data = await apiGet<{ requests: ApiRow[] }>("/api/access/my-requests");
  return data.requests ?? [];
}

async function loadPending(): Promise<ApiRow[]> {
  const data = await apiGet<{ requests: ApiRow[] }>("/api/access/requests", { status_value: "pending" });
  return data.requests ?? [];
}

const createForm: CreateForm = {
  title: "Tạo yêu cầu quyền",
  fields: [
    {
      key: "request_type",
      label: "Loại yêu cầu",
      type: "select",
      required: true,
      options: [
        { label: "Nâng mức mật", value: "clearance" },
        { label: "Thêm phòng ban", value: "department" },
      ],
    },
    { key: "requested_level", label: "Mức mật mong muốn", type: "select", options: SECURITY_LEVELS },
    { key: "requested_dept", label: "Phòng ban mong muốn", type: "text" },
    { key: "reason", label: "Lý do", type: "textarea" },
  ],
  submit: (values) => apiSend("/api/access/request", "POST", values),
};

function reqId(row: ApiRow): number {
  const keys = Object.keys(row);
  const hit = keys.find((k) => k.toLowerCase().includes("requestid")) || keys.find((k) => k.toLowerCase() === "id");
  return Number(hit ? row[hit] : num(row, "RequestID"));
}

const reviewActions: RowAction[] = [
  {
    label: "Duyệt",
    run: (r) => apiSend(`/api/access/requests/${reqId(r)}/resolve`, "POST", { decision: "approved" }),
  },
  {
    label: "Từ chối",
    severity: "warning",
    run: (r) => apiSend(`/api/access/requests/${reqId(r)}/resolve`, "POST", { decision: "rejected" }),
  },
];
</script>

<template>
  <div class="stacked-pages">
    <ResourcePage
      title="Yêu cầu quyền của tôi"
      eyebrow="Access"
      description="Gửi yêu cầu nâng mức mật hoặc thêm phòng ban."
      :load="loadMine"
      :create-form="createForm"
    />
    <ResourcePage
      v-if="canReview"
      title="Yêu cầu chờ duyệt"
      eyebrow="Access review"
      description="Duyệt hoặc từ chối các yêu cầu đang chờ."
      :load="loadPending"
      :row-actions="reviewActions"
    />
  </div>
</template>
