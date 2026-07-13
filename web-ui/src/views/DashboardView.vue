<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { loadDashboard } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { dashboardMetricLabel, dashboardMetricTarget } from "@/utils/dashboard";
import type { ApiRow } from "@/types";

type DashboardGroup = Record<string, number>;
type DashboardPayload = {
  stats?: DashboardGroup;
  document_lifecycle?: DashboardGroup;
  ingestion?: DashboardGroup;
  review?: DashboardGroup;
  usage?: DashboardGroup;
  rollout?: DashboardGroup;
  recent_documents?: ApiRow[];
  recent_failed_jobs?: ApiRow[];
  recent_chats?: ApiRow[];
};

const auth = useAuthStore();
const router = useRouter();
const loading = ref(true);
const payload = ref<DashboardPayload>({});
const error = ref("");

const GROUPS: Array<{ key: keyof DashboardPayload; title: string }> = [
  { key: "document_lifecycle", title: "Vòng đời tài liệu" },
  { key: "ingestion", title: "Ingest" },
  { key: "review", title: "Duyệt và chất lượng" },
  { key: "usage", title: "Sử dụng" },
  { key: "rollout", title: "Rollout phòng ban" },
];

const roles = computed(() => auth.user?.roles ?? []);
const roleSummary = computed(() => {
  if (roles.value.includes("platform_admin")) return "Toàn cảnh vận hành hệ thống";
  if (roles.value.includes("admin")) return "Công việc tài liệu và quản trị nội dung";
  if (roles.value.some((r) => ["reviewer", "knowledge_approver"].includes(r))) return "Công việc duyệt và quản trị tri thức";
  if (roles.value.includes("uploader")) return "Tiến độ tài liệu bạn phụ trách";
  return "Tài liệu và hoạt động của bạn";
});
const groups = computed(() => {
  const result = GROUPS.map((g) => ({ ...g, values: payload.value[g.key] as DashboardGroup | undefined }))
    .filter((g) => g.values && Object.keys(g.values).length);
  if (!result.length && payload.value.stats) return [{ key: "stats", title: "Tổng quan", values: payload.value.stats }];
  return result;
});

function label(key: string) { return dashboardMetricLabel(key); }
function target(key: string): string | null { return dashboardMetricTarget(key); }
function openMetric(key: string) { const to = target(key); if (to) void router.push(to); }
function cell(row: ApiRow, ...keys: string[]) {
  const value = keys.map((key) => row[key]).find((item) => item !== null && item !== undefined && item !== "");
  return value == null ? "—" : String(value);
}

onMounted(async () => {
  try { payload.value = await loadDashboard() as DashboardPayload; }
  catch (err) { error.value = err instanceof Error ? err.message : "Không tải được tổng quan"; }
  finally { loading.value = false; }
});
</script>

<template>
  <section class="content-page">
    <header class="page-header"><div><div class="eyebrow">Overview</div><h1>Tổng quan</h1><p class="page-subtitle" v-text="roleSummary"></p></div></header>
    <Message v-if="error" severity="error" v-text="error" />
    <div v-if="loading" class="loading-block"><ProgressSpinner /></div>
    <template v-else>
      <section v-for="group in groups" :key="group.key" class="dashboard-group">
        <h2 v-text="group.title"></h2>
        <div class="stat-grid">
          <Card v-for="(value, key) in group.values" :key="key" class="metric-card" :class="{ actionable: target(String(key)) }" @click="openMetric(String(key))">
            <template #title><span v-text="label(String(key))"></span></template>
            <template #content><strong class="stat-number" v-text="value"></strong><small v-if="target(String(key))">Xem chi tiết</small></template>
          </Card>
        </div>
      </section>
      <div class="two-column">
        <Card><template #title>Tài liệu gần đây</template><template #content>
          <ul v-if="payload.recent_documents?.length" class="activity-list"><li v-for="(row, i) in payload.recent_documents" :key="i"><strong>{{ cell(row, 'TenFile', 'OriginalFileName', 'file') }}</strong><span>{{ cell(row, 'Department', 'ThuMuc', 'dept') }} · {{ cell(row, 'LifecycleStatus', 'effective_status') }}</span></li></ul>
          <p v-else class="muted-text">Không có dữ liệu.</p>
        </template></Card>
        <Card v-if="payload.recent_failed_jobs"><template #title>Job lỗi gần đây</template><template #content>
          <ul v-if="payload.recent_failed_jobs.length" class="activity-list"><li v-for="(row, i) in payload.recent_failed_jobs" :key="i"><strong>{{ cell(row, 'TenFile', 'file') }}</strong><span>{{ cell(row, 'ErrorMessage', 'error') }}</span></li></ul>
          <p v-else class="muted-text">Không có job lỗi.</p>
        </template></Card>
      </div>
    </template>
  </section>
</template>

<style scoped>
.dashboard-group { margin-bottom: 1.5rem; }
.dashboard-group h2 { font-size: 1rem; margin: 0 0 .75rem; }
.metric-card.actionable { cursor: pointer; }
.metric-card.actionable:hover { transform: translateY(-1px); }
.metric-card small { display: block; margin-top: .4rem; color: var(--p-primary-color); }
.activity-list { list-style: none; padding: 0; margin: 0; display: grid; gap: .8rem; }
.activity-list li { display: grid; gap: .2rem; border-bottom: 1px solid var(--p-content-border-color); padding-bottom: .65rem; }
.activity-list span, .muted-text { color: var(--p-text-muted-color); font-size: .86rem; }
</style>
