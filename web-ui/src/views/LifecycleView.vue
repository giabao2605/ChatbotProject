<script setup lang="ts">
import StatView from "@/components/StatView.vue";
import { apiGet, apiSend } from "@/api/client";
import type { ToolbarAction } from "@/types";

async function load(): Promise<Record<string, unknown>> {
  return apiGet<Record<string, unknown>>("/api/lifecycle", { soon_days: 30 });
}

const toolbar: ToolbarAction[] = [
  {
    label: "Cập nhật hết hạn",
    confirm: "Đánh dấu các tài liệu quá hạn?",
    run: () => apiSend("/api/lifecycle/refresh-expired", "POST"),
  },
];
</script>

<template>
  <StatView
    title="Vòng đời tài liệu"
    eyebrow="Lifecycle"
    description="Tổng quan hết hạn, sắp hết hạn và cần rà soát."
    :load="load"
    :toolbar="toolbar"
  />
</template>
