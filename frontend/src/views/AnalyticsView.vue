<script setup lang="ts">
import { computed } from "vue";
import StatView from "@/components/StatView.vue";
import { apiGet } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { isRoleAllowed } from "@/authorization";

const auth = useAuthStore();
const canReviewAnalytics = computed(() => isRoleAllowed(auth.user?.roles, ["reviewer", "admin"]));
const canViewCacheAnalytics = computed(() => isRoleAllowed(auth.user?.roles, ["platform_admin"]));

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
      v-if="canReviewAnalytics"
      title="Báo cáo sử dụng"
      eyebrow="Analytics"
      description="Thống kê sử dụng 30 ngày gần nhất."
      :load="loadUsage"
    />
    <StatView
      v-if="canReviewAnalytics"
      title="Theo phòng ban"
      eyebrow="Analytics"
      description="Phân bổ truy vấn theo phòng ban."
      :load="loadDepartments"
    />
    <StatView
      v-if="canViewCacheAnalytics"
      title="Semantic cache"
      eyebrow="Analytics"
      description="Hiệu quả của semantic cache."
      :load="loadCache"
    />
  </div>
</template>
