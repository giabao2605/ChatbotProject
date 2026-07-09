<script setup lang="ts">
import { onMounted, ref } from "vue";
import { loadDashboard } from "@/api/client";
import type { ApiRow } from "@/types";

const loading = ref(true);
const stats = ref<Record<string, number>>({});
const recentDocuments = ref<ApiRow[]>([]);
const failedJobs = ref<ApiRow[]>([]);
const error = ref("");

// GD4: nhan tieng Viet cho cac khoa thong ke (thay vi in khoa ky thuat).
const STAT_LABELS: Record<string, string> = {
  total_docs: "Tổng tài liệu",
  pending_review: "Chờ duyệt",
  published_docs: "Đã xuất bản",
  running_jobs: "Job đang chạy",
  failed_jobs: "Job lỗi",
  today_chats: "Chat hôm nay",
  pending_feedback: "Feedback chờ xử lý",
};
function statLabel(key: string): string {
  return STAT_LABELS[key] ?? key.replace(/_/g, " ");
}

// GD4: backend tra dict co ten cot (_rows_to_json).
const docColumns = [
  { field: "DocID", header: "DocID" },
  { field: "TenFile", header: "Tên file" },
  { field: "ThuMuc", header: "Phòng ban" },
  { field: "ReviewStatus", header: "Duyệt" },
  { field: "LifecycleStatus", header: "Vòng đời" },
  { field: "NgayTaiLen", header: "Tải lên" },
];
const jobColumns = [
  { field: "JobID", header: "JobID" },
  { field: "TenFile", header: "Tên file" },
  { field: "ThuMuc", header: "Phòng ban" },
  { field: "Status", header: "Trạng thái" },
  { field: "ErrorMessage", header: "Lỗi" },
  { field: "UpdatedAt", header: "Cập nhật" },
];

function cell(row: ApiRow, field: string): string {
  const v = row[field];
  if (v === null || v === undefined || v === "") return "—";
  return String(v);
}

onMounted(async () => {
  try {
    const data = await loadDashboard();
    stats.value = data.stats ?? {};
    recentDocuments.value = (data.recent_documents ?? []) as ApiRow[];
    failedJobs.value = (data.recent_failed_jobs ?? []) as ApiRow[];
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

    <Message v-if="error" severity="error" v-text="error"></Message>
    <div v-if="loading" class="loading-block">
      <ProgressSpinner />
    </div>

    <template v-else>
      <div class="stat-grid">
        <Card v-for="(value, key) in stats" :key="key">
          <template #title><span v-text="statLabel(String(key))"></span></template>
          <template #content>
            <strong class="stat-number" v-text="value"></strong>
          </template>
        </Card>
      </div>

      <div class="two-column">
        <Card>
          <template #title>Tài liệu gần đây</template>
          <template #content>
            <div class="dash-table-wrap">
              <table v-if="recentDocuments.length" class="dash-table">
                <thead>
                  <tr>
                    <th v-for="col in docColumns" :key="col.field" v-text="col.header"></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, ri) in recentDocuments" :key="ri">
                    <td v-for="col in docColumns" :key="col.field" v-text="cell(row, col.field)"></td>
                  </tr>
                </tbody>
              </table>
              <p v-else class="muted-text">Không có dữ liệu</p>
            </div>
          </template>
        </Card>
        <Card>
          <template #title>Job lỗi gần đây</template>
          <template #content>
            <div class="dash-table-wrap">
              <table v-if="failedJobs.length" class="dash-table">
                <thead>
                  <tr>
                    <th v-for="col in jobColumns" :key="col.field" v-text="col.header"></th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, ri) in failedJobs" :key="ri">
                    <td v-for="col in jobColumns" :key="col.field" v-text="cell(row, col.field)"></td>
                  </tr>
                </tbody>
              </table>
              <p v-else class="muted-text">Không có job lỗi</p>
            </div>
          </template>
        </Card>
      </div>
    </template>
  </section>
</template>

<style scoped>
.dash-table-wrap {
  overflow-x: auto;
}
.dash-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}
.dash-table th,
.dash-table td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--p-surface-200, #e5e7eb);
  white-space: nowrap;
  max-width: 260px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.dash-table th {
  color: var(--p-text-muted-color, #6b7280);
  font-weight: 600;
}
.muted-text {
  color: var(--p-text-muted-color, #6b7280);
}
</style>
