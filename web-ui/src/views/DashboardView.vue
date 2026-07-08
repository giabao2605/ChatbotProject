<script setup lang="ts">
import { onMounted, ref } from "vue";
import { loadDashboard } from "@/api/client";

const loading = ref(true);
const stats = ref<Record<string, number>>({});
const recentDocuments = ref<unknown[]>([]);
const failedJobs = ref<unknown[]>([]);
const error = ref("");

onMounted(async () => {
  try {
    const data = await loadDashboard();
    stats.value = data.stats ?? {};
    recentDocuments.value = data.recent_documents ?? [];
    failedJobs.value = data.recent_failed_jobs ?? [];
  } catch (err) {
    error.value = err instanceof Error ? err.message : "Không tải được dashboard";
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <section class="content-page">
    <header class="page-header">
      <div>
        <div class="eyebrow">Overview</div>
        <h1>Tổng quan hệ thống</h1>
      </div>
    </header>

    <Message v-if="error" severity="error">{{ error }}</Message>
    <div v-if="loading" class="loading-block">
      <ProgressSpinner />
    </div>

    <div v-else class="stat-grid">
      <Card v-for="(value, key) in stats" :key="key">
        <template #title>{{ key }}</template>
        <template #content>
          <strong class="stat-number">{{ value }}</strong>
        </template>
      </Card>
    </div>

    <div class="two-column">
      <Card>
        <template #title>Tài liệu gần đây</template>
        <template #content>
          <pre>{{ recentDocuments }}</pre>
        </template>
      </Card>
      <Card>
        <template #title>Job lỗi gần đây</template>
        <template #content>
          <pre>{{ failedJobs }}</pre>
        </template>
      </Card>
    </div>
  </section>
</template>
