<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiSend, apiGet } from "@/api/client";
import { pickNum, pickStr } from "@/utils/rows";
import type { ApiRow, ResourceColumn, RowAction } from "@/types";

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ documents: ApiRow[] }>("/api/documents/pending-review");
  return (data.documents ?? []).map((row) => ({
    ...row,
    ExtractionSummary: summarizeExtraction(row),
  }));
}

const columns: ResourceColumn[] = [
  { field: "JobID", header: "Job", width: "72px" },
  { field: "TenFile", header: "Tệp", width: "24%" },
  { field: "ThuMuc", header: "Phòng ban", width: "120px" },
  { field: "UploadedBy", header: "Người tải", width: "110px" },
  { field: "UpdatedAt", header: "Cập nhật", width: "145px" },
  { field: "ExtractionSummary", header: "Kết quả ingest", width: "28%" },
  { field: "Domain", header: "Domain", width: "110px" },
  { field: "SecurityLevel", header: "Mức mật", kind: "tag", width: "110px" },
  { field: "Site", header: "Site", width: "90px" },
];

function parseReport(row: ApiRow): Record<string, unknown> | null {
  const raw = row.ExtractionReport;
  if (!raw) return null;
  if (typeof raw === "object" && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw !== "string") return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function summarizeExtraction(row: ApiRow): string {
  const report = parseReport(row);
  if (!report) return "Chưa có báo cáo ingest";
  const status = String(report.status ?? "unknown");
  const pages = Number(report.total_pages ?? 0);
  const chunks = Number(report.total_chunks ?? 0);
  const tables = Array.isArray(report.pages_table_extracted) ? report.pages_table_extracted.length : 0;
  const failed = Array.isArray(report.failed_pages) ? report.failed_pages.length : 0;
  const score = report.quality_score == null ? "" : `, chất lượng ${report.quality_score}`;
  const time = report.time_taken == null ? "" : `, ${Number(report.time_taken).toFixed(1)}s`;
  const parts = [`${status}`, `${pages} trang`, `${chunks} chunks`];
  if (tables) parts.push(`${tables} bảng`);
  if (failed) parts.push(`${failed} trang lỗi`);
  return `${parts.join(", ")}${score}${time}`;
}

function pickId(row: ApiRow, candidates: string[]): number | null {
  const value = pickNum(row, candidates);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function jobId(row: ApiRow): number | null {
  return pickId(row, ["JobID", "job_id"]);
}

function docId(row: ApiRow): number | null {
  return pickId(row, ["DocID", "doc_id"]);
}

type ReviewStep = { path: string; method: "POST" | "DELETE"; body?: unknown };

async function reviewAction(
  row: ApiRow,
  opts: {
    doc?: (docId: number) => ReviewStep;
    job: (jobId: number) => ReviewStep;
    requireDoc?: boolean;
  },
): Promise<void> {
  const doc = docId(row);
  const job = jobId(row);

  if (opts.requireDoc && doc === null) {
    throw new Error("Chưa tạo được tài liệu nên không thể xuất bản.");
  }

  if (opts.doc && doc !== null) {
    const step = opts.doc(doc);
    await apiSend(step.path, step.method, step.body);
  }

  if (job === null) throw new Error("Không tìm thấy JobID của dòng này.");
  const jobStep = opts.job(job);
  await apiSend(jobStep.path, jobStep.method, jobStep.body);
}

const rowActions: RowAction[] = [
  {
    label: "Xuất bản (version)",
    run: (r) =>
      reviewAction(r, {
        requireDoc: true,
        doc: (id) => ({ path: `/api/documents/${id}/publish-new-version`, method: "POST" }),
        job: (id) => ({ path: `/api/ingestion/jobs/${id}/publish`, method: "POST" }),
      }),
  },
  {
    label: "Xuất bản (variant)",
    run: (r) =>
      reviewAction(r, {
        requireDoc: true,
        doc: (id) => ({ path: `/api/documents/${id}/publish-new-variant`, method: "POST" }),
        job: (id) => ({ path: `/api/ingestion/jobs/${id}/publish`, method: "POST" }),
      }),
  },
  {
    label: "Xuất bản (độc lập)",
    run: (r) =>
      reviewAction(r, {
        requireDoc: true,
        doc: (id) => ({ path: `/api/documents/${id}/publish-standalone`, method: "POST" }),
        job: (id) => ({ path: `/api/ingestion/jobs/${id}/publish`, method: "POST" }),
      }),
  },
  {
    label: "Từ chối",
    severity: "warning",
    confirm: "Từ chối tài liệu này?",
    run: (r) =>
      reviewAction(r, {
        doc: (id) => ({ path: `/api/documents/${id}/reject`, method: "POST" }),
        job: (id) => ({
          path: `/api/ingestion/jobs/${id}/reject`,
          method: "POST",
          body: { reason: "Từ chối từ trang duyệt tài liệu" },
        }),
      }),
  },
  {
    label: "Lưu trữ",
    run: (r) =>
      reviewAction(r, {
        doc: (id) => ({ path: `/api/documents/${id}/archive`, method: "POST" }),
        job: (id) => ({ path: `/api/ingestion/jobs/${id}/publish`, method: "POST" }),
      }),
  },
  {
    label: "Xoá",
    severity: "danger",
    confirm: "Xoá vĩnh viễn?",
    run: (r) =>
      reviewAction(r, {
        doc: (id) => ({ path: `/api/documents/${id}`, method: "DELETE" }),
        job: (id) => ({ path: `/api/ingestion/jobs/${id}`, method: "DELETE" }),
      }),
  },
];

const bulk = reactive({
  jobs: [] as ApiRow[],
  selected: [] as number[],
  publishMode: "standalone",
  rejectReason: "",
  busy: false,
  error: "",
  notice: "",
});

async function loadBulkJobs() {
  const data = await apiGet<{ jobs: ApiRow[] }>("/api/ingestion/bulk-action-jobs");
  bulk.jobs = data.jobs ?? [];
  bulk.selected = bulk.selected.filter((id) => bulk.jobs.some((row) => jobId(row) === id));
}

function bulkItems() {
  return bulk.jobs
    .filter((row) => {
      const id = jobId(row);
      return id !== null && bulk.selected.includes(id);
    })
    .map((row) => ({ job_id: jobId(row), doc_id: docId(row) }));
}

async function runBulk(action: "publish" | "reject" | "delete") {
  const items = bulkItems();
  if (!items.length) {
    bulk.error = "Chọn ít nhất một job.";
    return;
  }
  bulk.busy = true;
  bulk.error = "";
  bulk.notice = "";
  try {
    const result = await apiSend<{ updated: number; failed: number }>("/api/documents/review/bulk", "POST", {
      items,
      action,
      publish_mode: bulk.publishMode,
      reason: bulk.rejectReason,
    });
    bulk.notice = `Hoàn tất: ${result.updated} thành công, ${result.failed} thất bại`;
    await loadBulkJobs();
  } catch (err) {
    bulk.error = err instanceof Error ? err.message : "Lỗi";
  } finally {
    bulk.busy = false;
  }
}

const bmeta = reactive({
  docs: [] as ApiRow[],
  departments: [] as string[],
  selected: [] as number[],
  dept: "",
  domain: "",
  busy: false,
  error: "",
  notice: "",
  values: {
    title: "",
    summary: "",
    tags: "",
    doc_number: "",
    owner_signer: "",
    effective_status: "",
    language: "",
  } as Record<string, string>,
});

async function loadBulkMeta() {
  const data = await apiGet<{ documents: ApiRow[]; departments: string[] }>("/api/documents/bulk-meta", {
    dept: bmeta.dept,
    domain: bmeta.domain,
  });
  bmeta.docs = data.documents ?? [];
  bmeta.departments = data.departments ?? [];
  bmeta.selected = bmeta.selected.filter((id) => bmeta.docs.some((row) => docId(row) === id));
}

async function applyBulkMeta() {
  if (!bmeta.selected.length) {
    bmeta.error = "Chọn ít nhất một tài liệu.";
    return;
  }
  const metadata = Object.fromEntries(
    Object.entries(bmeta.values)
      .map(([k, v]) => [k, String(v ?? "").trim()])
      .filter(([, v]) => v),
  );
  if (bmeta.domain) metadata.domain = bmeta.domain;
  if (!Object.keys(metadata).length) {
    bmeta.error = "Nhập ít nhất một metadata để cập nhật.";
    return;
  }
  bmeta.busy = true;
  bmeta.error = "";
  bmeta.notice = "";
  try {
    const result = await apiSend<{ updated: number; failed: number }>("/api/documents/bulk-metadata", "PATCH", {
      doc_ids: bmeta.selected,
      metadata,
    });
    bmeta.notice = `Đã cập nhật: ${result.updated} thành công, ${result.failed} thất bại`;
    await loadBulkMeta();
  } catch (err) {
    bmeta.error = err instanceof Error ? err.message : "Lỗi";
  } finally {
    bmeta.busy = false;
  }
}

onMounted(async () => {
  await Promise.all([loadBulkJobs(), loadBulkMeta()]);
});
</script>

<template>
  <div class="stacked-pages">
    <ResourcePage
      title="Duyệt tài liệu"
      eyebrow="Review"
      description="Tài liệu đang chờ duyệt. Chọn hình thức xuất bản hoặc từ chối."
      :load="load"
      :columns="columns"
      :row-actions="rowActions"
    />

    <section class="content-page">
      <header class="page-header">
        <div>
          <div class="eyebrow">Bulk review</div>
          <h1>Thao tác hàng loạt</h1>
          <p class="page-subtitle">Publish, reject hoặc xóa nhiều job giống tab Streamlit cũ.</p>
        </div>
        <div class="page-actions">
          <Button label="Tải lại" severity="secondary" outlined :disabled="bulk.busy" @click="loadBulkJobs" />
        </div>
      </header>
      <Message v-if="bulk.error" severity="error" v-text="bulk.error"></Message>
      <Message v-if="bulk.notice" severity="success" v-text="bulk.notice"></Message>
      <Card>
        <template #content>
          <p v-if="!bulk.jobs.length" class="muted-text">Không có job nào đủ điều kiện.</p>
          <div v-else class="bulk-list">
            <label v-for="row in bulk.jobs" :key="String(jobId(row))" class="check-row">
              <input v-model="bulk.selected" type="checkbox" :value="jobId(row)" />
              <span>
                [{{ pickStr(row, ["Status"]) }}] {{ pickStr(row, ["TenFile"]) }}
                (Job {{ jobId(row) }}, Doc {{ docId(row) || "N/A" }})
              </span>
            </label>
          </div>
          <div class="bulk-controls">
            <label class="stack-field">
              <span>Kiểu publish</span>
              <select v-model="bulk.publishMode" class="native-select">
                <option value="standalone">Tài liệu độc lập</option>
                <option value="new_variant">Variant mới</option>
                <option value="new_version">Version mới</option>
              </select>
            </label>
            <label class="stack-field">
              <span>Lý do reject</span>
              <InputText v-model="bulk.rejectReason" />
            </label>
          </div>
          <div class="form-actions">
            <Button label="Publish đã chọn" :loading="bulk.busy" @click="runBulk('publish')" />
            <Button label="Reject đã chọn" severity="warning" outlined :disabled="bulk.busy" @click="runBulk('reject')" />
            <Button
              label="Xóa đã chọn"
              severity="danger"
              outlined
              :disabled="bulk.busy"
              @click="runBulk('delete')"
            />
          </div>
        </template>
      </Card>
    </section>

    <section class="content-page">
      <header class="page-header">
        <div>
          <div class="eyebrow">Bulk metadata</div>
          <h1>Sửa metadata hàng loạt</h1>
          <p class="page-subtitle">Lọc tài liệu, chọn nhiều dòng và áp cùng metadata.</p>
        </div>
        <div class="page-actions">
          <Button label="Tải lại" severity="secondary" outlined :disabled="bmeta.busy" @click="loadBulkMeta" />
        </div>
      </header>
      <Message v-if="bmeta.error" severity="error" v-text="bmeta.error"></Message>
      <Message v-if="bmeta.notice" severity="success" v-text="bmeta.notice"></Message>
      <Card>
        <template #content>
          <div class="bulk-controls">
            <label class="stack-field">
              <span>Phòng ban</span>
              <select v-model="bmeta.dept" class="native-select" @change="loadBulkMeta">
                <option value="">Tất cả</option>
                <option v-for="dept in bmeta.departments" :key="dept" :value="dept" v-text="dept"></option>
              </select>
            </label>
            <label class="stack-field">
              <span>Domain</span>
              <select v-model="bmeta.domain" class="native-select" @change="loadBulkMeta">
                <option value="">Tất cả</option>
                <option value="mechanical">mechanical</option>
                <option value="tabular">tabular</option>
                <option value="generic">generic</option>
              </select>
            </label>
          </div>
          <p v-if="!bmeta.docs.length" class="muted-text">Không có tài liệu nào.</p>
          <div v-else class="bulk-list">
            <label v-for="row in bmeta.docs" :key="String(docId(row))" class="check-row">
              <input v-model="bmeta.selected" type="checkbox" :value="docId(row)" />
              <span>[{{ docId(row) }}] {{ pickStr(row, ["TenFile", "OriginalFileName"]) }} ({{ pickStr(row, ["ThuMuc", "Department"]) }})</span>
            </label>
          </div>
          <div class="meta-grid">
            <label class="stack-field">
              <span>Tiêu đề</span>
              <InputText v-model="bmeta.values.title" />
            </label>
            <label class="stack-field">
              <span>Tags</span>
              <InputText v-model="bmeta.values.tags" />
            </label>
            <label class="stack-field">
              <span>Số hiệu</span>
              <InputText v-model="bmeta.values.doc_number" />
            </label>
            <label class="stack-field">
              <span>Người ký / chủ quản</span>
              <InputText v-model="bmeta.values.owner_signer" />
            </label>
            <label class="stack-field">
              <span>Ngôn ngữ</span>
              <InputText v-model="bmeta.values.language" />
            </label>
            <label class="stack-field">
              <span>Trạng thái hiệu lực</span>
              <InputText v-model="bmeta.values.effective_status" />
            </label>
            <label class="stack-field meta-summary">
              <span>Tóm tắt</span>
              <textarea v-model="bmeta.values.summary" rows="3" class="native-select"></textarea>
            </label>
          </div>
          <div class="form-actions">
            <Button label="Áp dụng metadata" :loading="bmeta.busy" @click="applyBulkMeta" />
          </div>
        </template>
      </Card>
    </section>
  </div>
</template>

<style scoped>
.bulk-list {
  display: grid;
  gap: 0.5rem;
  max-height: 320px;
  overflow: auto;
  padding: 0.25rem 0;
}
.check-row {
  display: flex;
  align-items: center;
  gap: 0.55rem;
}
.bulk-controls,
.meta-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 0.85rem;
  margin: 1rem 0;
}
.meta-summary {
  grid-column: 1 / -1;
}
</style>
