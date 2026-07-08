<script setup lang="ts">
import StatView from "@/components/StatView.vue";
import { apiGet } from "@/api/client";

async function loadUsage(): Promise<Record<string, unknown>> {
  return apiGet<Record<string, unknown>>("/api/analytics/usage", { days: 30 });
}
async function loadDepartments(): Promise<Record<string, unknown>> {
  return apiGet<Record<string, unknown>>("/api/analytics/departments", { days: 30 });
}
async function loadCache(): Promise<Record<string, unknown>> {
  return apiGet<Record<string, unknown>>("/api/analytics/cache");
}
</script>

<template>
  <div class="stacked-pages">
    <StatView
      title="Báo cáo sử dụng"
      eyebrow="Analytics"
      description="Thống kê sử dụng 30 ngày gần nhất."
      :load="loadUsage"
    />
    <StatView
      title="Theo phòng ban"
      eyebrow="Analytics"
      description="Phân bổ truy vấn theo phòng ban."
      :load="loadDepartments"
    />
    <StatView
      title="Semantic cache"
      eyebrow="Analytics"
      description="Hiệu quả của semantic cache."
      :load="loadCache"
    />
  </div>
</template>
