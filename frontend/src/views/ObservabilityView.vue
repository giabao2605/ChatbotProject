<script setup lang="ts">
import StatView from "@/components/StatView.vue";
import { t } from "@/i18n";
import { apiGet } from "@/api/client";

async function load(): Promise<Record<string, unknown>> {
  const result = await apiGet<Record<string, unknown>>("/api/analytics/observability", { days: 7 });
  return { ...result, top_costly: Array.isArray(result.top_costly) ? result.top_costly.map(row => ({
    ...row, question: typeof row.question === 'string' && row.question.trim() ? row.question : t('observability.noQuestion'),
  })) : result.top_costly };

}
</script>

<template>
  <StatView
    title="Observability"
    eyebrow="Operations"
    description="Dành cho platform_admin: sức khỏe hệ thống, độ trễ và tỷ lệ lỗi 7 ngày gần nhất."
    :load="load"
  />
</template>
