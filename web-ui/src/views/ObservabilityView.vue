<script setup lang="ts">
import StatView from "@/components/StatView.vue";
import { apiGet } from "@/api/client";

async function load(): Promise<Record<string, unknown>> {
  return apiGet<Record<string, unknown>>("/api/analytics/observability", { days: 7 });
}
</script>

<template>
  <StatView
    title="Observability"
    eyebrow="Operations"
    description="Sức khỏe hệ thống, độ trễ và tỷ lệ lỗi 7 ngày gần nhất."
    :load="load"
  />
</template>
