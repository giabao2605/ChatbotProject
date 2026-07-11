<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiSend, apiGet } from "@/api/client";
import { pickNum, pickStr } from "@/utils/rows";
import type { ApiRow, ResourceColumn, RowAction } from "@/types";

type ContractIssue = { field?: string; code?: string; message?: string };
const router = useRouter();
const contractDialog = reactive({ visible: false, docId: 0, issues: [] as ContractIssue[] });

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ documents: ApiRow[] }>("/api/documents/pending-review");
  return (data.documents ?? []).map((row) => ({
    ...row,
    ExtractionSummary: summarizeExtraction(row),
    QualityDetails: qualityDetails(row),
  }));
}

const columns: ResourceColumn[] = [
  { field: "JobID", header: "Job", width: "72px" },
  { field: "TenFile", header: "Tệp", width: "24%" },
  { field: "ThuMuc", header: "Phòng ban", width: "120px" },
  { field: "UploadedBy", header: "Người tải", width: "110px" },
  { field: "UpdatedAt", header: "Cập nhật", width: "145px" },
  { field: "ExtractionSummary", header: "Kết quả ingest", width: "28%" },
  { field: "QualityDetails", header: "Chi tiết chất lượng", width: "26%" },
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

function qualityDetails(row: ApiRow): string {
  const report = parseReport(row);
  if (!report) return "Chưa có dữ liệu giải thích điểm.";
  const policy = String(report.quality_policy_version ?? report.policy_version ?? "chưa ghi phiên bản");
  const rawReasons = report.quality_reasons ?? report.reason_codes ?? report.reasons;
  const reasons = Array.isArray(rawReasons)
    ? rawReasons.map((item) => typeof item === "string" ? item : JSON.stringify(item)).filter(Boolean)
    : [];
  const rawComponents = report.quality_components ?? report.score_components;
  const components = rawComponents && typeof rawComponents === "object" && !Array.isArray(rawComponents)
    ? Object.entries(rawComponents as Record<string, unknown>).map(([key, value]) => `${key}: ${String(value)}`)
    : [];
  return [`Chính sách ${policy}`, ...components, ...reasons].join("; ");
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

function actionError(result: unknown): string | null {
  if (!result || typeof result !== "object" || !("ok" in result)) return null;
  if ((result as { ok?: boolean }).ok !== false) return null;
  const payload = result as {
    error?: string;
    validation?: { issues?: Array<{ message?: string }> };
  };
  const issues = payload.validation?.issues?.map((item) => item.message).filter(Boolean) ?? [];
  return issues.length ? issues.join("; ") : payload.error || "Thao tác thất bại.";
}

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

  if (opts.requireDoc && doc !== null) {
    const validation = await apiGet<{
      valid?: boolean;
      ok?: boolean;
      issues?: ContractIssue[];
    }>(`/api/documents/${doc}/publish-contract`);
    const issues = validation.issues ?? [];
    if (validation.valid === false || validation.ok === false || issues.length) {
      contractDialog.docId = doc;
      contractDialog.issues = issues.length ? issues : [{ code: "invalid_contract", message: "Tài liệu chưa đáp ứng publish contract." }];
      contractDialog.visible = true;
      const detail = issues.map((issue) => {
        const field = issue.field ? `[${issue.field}] ` : "";
        return `${field}${issue.message || issue.code || "Metadata chưa hợp lệ"}`;
      });
      throw new Error(`Không thể publish. ${detail.join("; ") || "Tài liệu chưa đáp ứng publish contract."}`);
    }
  }

  if (opts.doc && doc !== null) {
    const step = opts.doc(doc);
    const result = await apiSend(step.path, step.method, step.body);
    const error = actionError(result);
    if (error) throw new Error(error);
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
    const result = await apiSend<{
      updated: number;
      failed: number;
      failures?: Array<{ error?: string; validation?: { issues?: Array<{ message?: string }> } }>;
    }>("/api/documents/review/bulk", "POST", {
      items,
      action,
      publish_mode: bulk.publishMode,
      reason: bulk.rejectReason,
    });
    bulk.notice = `Hoàn tất: ${result.updated} thành công, ${result.failed} thất bại`;
    if (result.failures?.length) {
      const messages = result.failures.flatMap((failure) => {
        const issues = failure.validation?.issues?.map((issue) => issue.message).filter(Boolean) ?? [];
        return issues.length ? issues : [failure.error || "Publish thất bại"];
      });
      bulk.error = messages.join("; ");
    }
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

    <ResourcePage
      title="Danh sách tài liệu chờ duyệt"
      eyebrow="Review queue"
      description="Danh sách nằm cuối trang sau các công cụ hàng loạt. Chọn hình thức xuất bản hoặc từ chối trên từng dòng."
      :load="load"
      :columns="columns"
      :row-actions="rowActions"
    />
  </div>

  <Dialog v-model:visible="contractDialog.visible" header="Không thể publish" modal :style="{ width: '680px' }">
    <Message severity="warn">Tài liệu chưa đáp ứng hợp đồng metadata bắt buộc. Hãy sửa các trường dưới đây rồi kiểm tra lại.</Message>
    <div class="contract-table-wrap">
      <table class="contract-table">
        <thead><tr><th>Trường</th><th>Mã lỗi</th><th>Cần xử lý</th></tr></thead>
        <tbody><tr v-for="(issue, index) in contractDialog.issues" :key="`${issue.field}-${issue.code}-${index}`"><td>{{ issue.field || "Metadata" }}</td><td><code>{{ issue.code || "invalid" }}</code></td><td>{{ issue.message || "Giá trị chưa hợp lệ" }}</td></tr></tbody>
      </table>
    </div>
    <template #footer>
      <Button label="Đóng" severity="secondary" outlined @click="contractDialog.visible = false" />
      <Button label="Mở kho tài liệu để sửa metadata" @click="router.push({ path: '/documents', query: { tab: 'effective', doc: contractDialog.docId } }); contractDialog.visible = false" />
    </template>
  </Dialog>
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
.contract-table-wrap { overflow-x: auto; margin-top: 1rem; }
.contract-table { width: 100%; border-collapse: collapse; }
.contract-table th, .contract-table td { text-align: left; padding: .65rem; border-bottom: 1px solid var(--p-content-border-color); vertical-align: top; }
</style>
