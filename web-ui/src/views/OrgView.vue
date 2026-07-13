<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { isRoleAllowed } from "@/authorization";
import { useAuthStore } from "@/stores/auth";
import { pickStr } from "@/utils/rows";
import {
  filterRolloutReadiness,
  MANAGED_ROLLOUT_STATUSES,
  normalizeRolloutReadiness,
} from "@/utils/rollout-readiness";
import type { ApiRow, CreateForm, ResourceFilter, RowAction } from "@/types";

type Opt = { label: string; value: string };

const auth = useAuthStore();
const canManagePlatform = computed(() => isRoleAllowed(auth.user?.roles, ["platform_admin"]));

async function loadDepartments(): Promise<ApiRow[]> {
  const data = await apiGet<{ departments: ApiRow[] }>("/api/catalog/departments", { active_only: false });
  return data.departments ?? [];
}

async function loadSites(): Promise<ApiRow[]> {
  const data = await apiGet<{ sites: ApiRow[] }>("/api/catalog/sites", { active_only: false });
  return data.sites ?? [];
}

async function loadKnowledgeGovernance(): Promise<ApiRow[]> {
  const data = await apiGet<{ governance: ApiRow[] }>("/api/catalog/knowledge-governance");
  return data.governance ?? [];
}

async function loadDomainProfiles(): Promise<ApiRow[]> {
  const data = await apiGet<{ profiles: ApiRow[] }>("/api/catalog/domain-profiles");
  return data.profiles ?? [];
}

async function loadMissingSiteDocuments(): Promise<ApiRow[]> {
  const data = await apiGet<{ documents: ApiRow[] }>("/api/catalog/missing-site-documents", { limit: 500 });
  return data.documents ?? [];
}

async function loadRolloutReadiness(filters: Record<string, unknown> = {}): Promise<ApiRow[]> {
  const data = await apiGet<{ departments: ApiRow[] }>("/api/catalog/rollout/readiness");
  const rows = (data.departments ?? []).map(normalizeRolloutReadiness);
  return filterRolloutReadiness(rows, filters);
}

function csvList(value: unknown): string[] {
  return String(value ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

// Danh sach phong ban cho dropdown "chuyen du lieu".
const deptOptions = ref<Opt[]>([]);
onMounted(async () => {
  try {
    const rows = await loadDepartments();
    deptOptions.value = rows
      .map((r) => {
        const code = pickStr(r, ["code", "DeptCode"]);
        const name = pickStr(r, ["name", "DeptName"]);
        return { value: code, label: name ? `${code} — ${name}` : code };
      })
      .filter((d) => d.value);
  } catch {
    /* giu trong */
  }
});

const deptForm: CreateForm = {
  title: "Thêm phòng ban",
  fields: [
    { key: "code", label: "Mã phòng ban", required: true },
    { key: "name", label: "Tên", required: true },
    { key: "domain", label: "Domain" },
    { key: "site", label: "Site" },
  ],
  submit: (values) => apiSend("/api/catalog/departments", "POST", values),
};

const siteForm: CreateForm = {
  title: "Thêm site",
  fields: [
    { key: "code", label: "Mã site", required: true },
    { key: "name", label: "Tên", required: true },
  ],
  submit: (values) => apiSend("/api/catalog/sites", "POST", values),
};

const governanceColumns = [
  { field: "department_code", header: "Phòng ban", kind: "code" as const },
  { field: "knowledge_owner_user_id", header: "Owner UserID" },
  { field: "knowledge_approver_user_id", header: "Approver UserID" },
  { field: "taxonomy_version", header: "Taxonomy" },
  { field: "external_processing_policy", header: "External policy", kind: "tag" as const },
  { field: "is_active", header: "Đang áp dụng", kind: "bool" as const },
];

const governanceForm: CreateForm = {
  title: "Thiết lập Knowledge Owner / Approver",
  triggerLabel: "Thiết lập governance",
  fields: [
    { key: "department_code", label: "Mã phòng ban", required: true },
    { key: "knowledge_owner_user_id", label: "Knowledge Owner UserID", type: "number", required: true },
    { key: "knowledge_approver_user_id", label: "Knowledge Approver UserID", type: "number", required: true },
    { key: "taxonomy_version", label: "Taxonomy version", required: true, placeholder: "v1" },
    {
      key: "external_processing_policy",
      label: "External processing policy",
      type: "select",
      options: [
        { label: "all_external", value: "all_external" },
        { label: "internal_only", value: "internal_only" },
      ],
      required: true,
    },
  ],
  submit: (values) => {
    const code = String(values.department_code ?? "").trim();
    return apiSend(`/api/catalog/departments/${encodeURIComponent(code)}/knowledge-governance`, "PUT", {
      knowledge_owner_user_id: values.knowledge_owner_user_id,
      knowledge_approver_user_id: values.knowledge_approver_user_id,
      taxonomy_version: values.taxonomy_version,
      external_processing_policy: values.external_processing_policy || "all_external",
      is_active: true,
    });
  },
};

const domainProfileColumns = [
  { field: "department_code", header: "Phòng ban", kind: "code" as const },
  { field: "document_types", header: "Loại tài liệu" },
  { field: "required_metadata", header: "Metadata bắt buộc" },
  { field: "router_patterns", header: "Router patterns" },
  { field: "parent_context_enabled", header: "Parent context", kind: "bool" as const },
];

const domainProfileForm: CreateForm = {
  title: "Thiết lập Domain Profile",
  triggerLabel: "Thiết lập profile",
  fields: [
    { key: "department_code", label: "Mã phòng ban", required: true },
    { key: "document_types", label: "Loại tài liệu, ngăn cách dấu phẩy", type: "textarea", required: true },
    { key: "required_metadata", label: "Metadata bắt buộc, ngăn cách dấu phẩy", type: "textarea", required: true },
    { key: "router_patterns", label: "Router patterns, ngăn cách dấu phẩy", type: "textarea" },
    { key: "disable_parent_context", label: "Tắt parent context", type: "checkbox" },
  ],
  submit: (values) => {
    const code = String(values.department_code ?? "").trim();
    return apiSend(`/api/catalog/departments/${encodeURIComponent(code)}/domain-profile`, "PUT", {
      document_types: csvList(values.document_types),
      required_metadata: csvList(values.required_metadata),
      router_patterns: csvList(values.router_patterns),
      parent_context_enabled: !Boolean(values.disable_parent_context),
      is_active: true,
    });
  },
};

const missingSiteColumns = [
  { field: "doc_id", header: "DocID", kind: "code" as const },
  { field: "file_name", header: "Tài liệu" },
  { field: "owner_department", header: "Phòng ban" },
  { field: "lifecycle_status", header: "Lifecycle", kind: "tag" as const },
  { field: "review_status", header: "Review", kind: "tag" as const },
];

const siteBackfillForm: CreateForm = {
  title: "Backfill site cho tài liệu",
  triggerLabel: "Backfill site",
  fields: [
    { key: "doc_id", label: "DocID", type: "number", required: true },
    { key: "site", label: "Mã site", required: true },
  ],
  submit: (values) => {
    const docId = String(values.doc_id ?? "").trim();
    return apiSend(`/api/documents/${encodeURIComponent(docId)}/site`, "PATCH", {
      site: values.site,
    });
  },
};

const rolloutReadinessColumns = [
  { field: "department_code", header: "Phòng ban", kind: "code" as const },
  { field: "wave_number", header: "Wave" },
  { field: "rollout_status", header: "Trạng thái", kind: "tag" as const },
  { field: "servable_document_count", header: "Tài liệu phục vụ" },
  { field: "evaluation_question_count", header: "Câu eval" },
  { field: "evaluation_question_target", header: "Mục tiêu" },
  { field: "missing_site_documents", header: "Thiếu site" },
  { field: "missing_prerequisites_display", header: "Điều kiện còn thiếu" },
  { field: "ready_for_next_wave", header: "Đủ gate", kind: "bool" as const },
];

const rolloutFilters: ResourceFilter[] = [
  {
    key: "wave_number",
    label: "Wave",
    type: "select",
    options: [1, 2, 3, 4].map((value) => ({ label: String(value), value })),
  },
  {
    key: "rollout_status",
    label: "Trạng thái",
    type: "select",
    options: ["unplanned", "planned", "pilot", "dark_launch", "active", "blocked"].map((value) => ({
      label: value,
      value,
    })),
  },
];

const rolloutPlanForm: CreateForm = {
  title: "Xếp phòng ban vào rollout wave",
  triggerLabel: "Cập nhật rollout plan",
  fields: [
    { key: "department_code", label: "Mã phòng ban", required: true },
    {
      key: "wave_number",
      label: "Wave",
      type: "select",
      options: [
        { label: "1 - Pilot", value: 1 },
        { label: "2", value: 2 },
        { label: "3", value: 3 },
        { label: "4", value: 4 },
      ],
      required: true,
    },
    {
      key: "rollout_status",
      label: "Trạng thái",
      type: "select",
      options: MANAGED_ROLLOUT_STATUSES.map((value) => ({ label: value, value })),
      required: true,
    },
    { key: "evaluation_question_target", label: "Số câu evaluation tối thiểu", type: "number", required: true },
  ],
  submit: (values) => {
    const code = String(values.department_code ?? "").trim();
    return apiSend(`/api/catalog/departments/${encodeURIComponent(code)}/rollout-plan`, "PUT", {
      wave_number: values.wave_number,
      rollout_status: values.rollout_status || "planned",
      evaluation_question_target: values.evaluation_question_target || 75,
    });
  },
};

const evaluationGateForm: CreateForm = {
  title: "Ghi nhận evaluation gate thực tế",
  triggerLabel: "Ghi nhận kết quả gate",
  fields: [
    { key: "department_code", label: "Mã phòng ban", required: true },
    { key: "batch_id", label: "Batch ID", required: true, placeholder: "pilot-kythuat-2026-07" },
    { key: "question_count", label: "Số câu đã chạy", type: "number", required: true },
    { key: "source_top5_rate", label: "Source trong top 5 (0..1)", type: "number", required: true },
    { key: "citation_or_refusal_rate", label: "Citation hoặc refusal đúng (0..1)", type: "number", required: true },
    { key: "evidence_support_rate", label: "Evidence support (0..1)", type: "number", required: true },
    { key: "rbac_site_publication_leaks", label: "Số lỗi RBAC/site/publication", type: "number", required: true },
    { key: "notes", label: "Ghi chú evidence", type: "textarea" },
  ],
  submit: (values) => {
    const code = String(values.department_code ?? "").trim();
    return apiSend(`/api/catalog/departments/${encodeURIComponent(code)}/evaluation-gate`, "POST", {
      batch_id: values.batch_id,
      question_count: values.question_count,
      source_top5_rate: values.source_top5_rate,
      citation_or_refusal_rate: values.citation_or_refusal_rate,
      evidence_support_rate: values.evidence_support_rate,
      rbac_site_publication_leaks: values.rbac_site_publication_leaks,
      notes: values.notes || null,
    });
  },
};

function deptCode(row: ApiRow): string {
  return pickStr(row, ["code", "MaPhong", "DepartmentCode", "department"]);
}

function siteCode(row: ApiRow): string {
  return pickStr(row, ["code", "SiteCode"]);
}

function siteName(row: ApiRow): string {
  return pickStr(row, ["name", "SiteName"]);
}

// Site khong co endpoint xoa cung; dung upsert de bat/tat IsActive (vo hieu hoa).
const siteActions: RowAction[] = [
  {
    label: "Kích hoạt",
    visible: (r) => r.is_active === false || r.is_active === 0,
    run: (r) =>
      apiSend("/api/catalog/sites", "POST", { code: siteCode(r), name: siteName(r), is_active: true }),
  },
  {
    label: "Vô hiệu hóa",
    severity: "warning",
    confirm: "Vô hiệu hóa site này?",
    run: (r) =>
      apiSend("/api/catalog/sites", "POST", { code: siteCode(r), name: siteName(r), is_active: false }),
  },
];

// ---- Hop thoai: doi trang thai (dropdown) + chuyen du lieu (reassign) ----
const dlg = reactive({
  visible: false,
  busy: false,
  error: "",
  mode: "status" as "status" | "reassign",
  sourceCode: "",
  status: "active",
  targetCode: "",
  moveUsers: true,
});
let resolveDlg: (() => void) | null = null;
let rejectDlg: ((e: unknown) => void) | null = null;

const dialogTitle = computed(() =>
  dlg.mode === "status" ? `Đổi trạng thái — ${dlg.sourceCode}` : `Chuyển dữ liệu — ${dlg.sourceCode}`,
);

// Loai bo phong ban nguon khoi danh sach dich.
const targetOptions = computed<Opt[]>(() => deptOptions.value.filter((o) => o.value !== dlg.sourceCode));

function openDialog(row: ApiRow, mode: "status" | "reassign"): Promise<void> {
  return new Promise((resolve, reject) => {
    resolveDlg = resolve;
    rejectDlg = reject;
    dlg.mode = mode;
    dlg.sourceCode = deptCode(row);
    dlg.error = "";
    dlg.status = pickStr(row, ["status", "Status"]) || "active";
    dlg.targetCode = "";
    dlg.moveUsers = true;
    dlg.visible = true;
  });
}

async function submit() {
  dlg.busy = true;
  dlg.error = "";
  try {
    if (dlg.mode === "status") {
      await apiSend(`/api/catalog/departments/${dlg.sourceCode}/status`, "PATCH", { status: dlg.status });
    } else {
      if (!dlg.targetCode) {
        dlg.error = "Chọn phòng ban đích.";
        dlg.busy = false;
        return;
      }
      await apiSend("/api/catalog/departments/reassign", "POST", {
        source_code: dlg.sourceCode,
        target_code: dlg.targetCode,
        move_users: dlg.moveUsers,
      });
    }
    dlg.visible = false;
    resolveDlg?.();
    resolveDlg = null;
    rejectDlg = null;
  } catch (err) {
    dlg.error = err instanceof Error ? err.message : "Lỗi";
  } finally {
    dlg.busy = false;
  }
}

function cancel() {
  dlg.visible = false;
  rejectDlg?.(new Error(""));
  resolveDlg = null;
  rejectDlg = null;
}

const deptActions: RowAction[] = [
  { label: "Đổi trạng thái", run: (r) => openDialog(r, "status") },
  { label: "Chuyển dữ liệu", run: (r) => openDialog(r, "reassign") },
  {
    label: "Lưu trữ",
    severity: "warning",
    confirm: "Lưu trữ phòng ban này?",
    run: (r) => apiSend(`/api/catalog/departments/${deptCode(r)}/archive`, "POST", { force: false }),
  },
];
</script>

<template>
  <div class="stacked-pages">
    <ResourcePage
      title="Phòng ban"
      eyebrow="Catalog"
      description="Danh mục phòng ban và trạng thái."
      :load="loadDepartments"
      :create-form="deptForm"
      :row-actions="deptActions"
    />
    <ResourcePage
      title="Site"
      eyebrow="Catalog"
      description="Danh mục site / nhà máy."
      :load="loadSites"
      :create-form="siteForm"
      :row-actions="siteActions"
    />
    <ResourcePage
      title="Knowledge governance"
      eyebrow="Knowledge platform"
      description="Mỗi phòng cần Owner và Approver thật trước khi tài liệu có thể publish. Admin global-read không tự động có quyền duyệt thay."
      :load="loadKnowledgeGovernance"
      :columns="governanceColumns"
      :create-form="canManagePlatform ? governanceForm : undefined"
    />
    <ResourcePage
      title="Domain profiles"
      eyebrow="Knowledge platform"
      description="Catalog loại tài liệu, metadata bắt buộc và rule router riêng cho từng phòng ban."
      :load="loadDomainProfiles"
      :columns="domainProfileColumns"
      :create-form="canManagePlatform ? domainProfileForm : undefined"
    />
    <ResourcePage
      title="Tài liệu thiếu site"
      eyebrow="Migration gate"
      description="Các tài liệu này phải được backfill site và duyệt lại trước khi publish."
      :load="loadMissingSiteDocuments"
      :columns="missingSiteColumns"
      :create-form="siteBackfillForm"
    />
    <ResourcePage
      title="Rollout readiness"
      eyebrow="3 → 4 → 4 → 4"
      description="Chỉ chuyển sang dark launch hoặc active khi rollout plan, taxonomy, governance, domain profile, site, corpus phục vụ, Owner/Approver, bộ câu evaluation và evaluation gate đều đạt. Trạng thái pilot chỉ dành cho Wave 1 được bootstrap; UI không dùng pilot để bỏ qua gate."
      :load="loadRolloutReadiness"
      :columns="rolloutReadinessColumns"
      :filters="rolloutFilters"
      :create-form="canManagePlatform ? rolloutPlanForm : undefined"
    />
    <ResourcePage
      title="Evaluation gate"
      eyebrow="Evidence required"
      description="Chỉ nhập kết quả từ bộ câu hỏi và benchmark đã chạy thực tế; hệ thống tự tính pass/fail theo threshold của plan."
      :load="loadRolloutReadiness"
      :columns="rolloutReadinessColumns"
      :filters="rolloutFilters"
      :create-form="canManagePlatform ? evaluationGateForm : undefined"
    />
  </div>

  <Dialog v-model:visible="dlg.visible" :header="dialogTitle" modal :style="{ width: '460px' }" @hide="cancel">
    <div class="stack-form">
      <Message v-if="dlg.error" severity="error" v-text="dlg.error"></Message>
      <label v-if="dlg.mode === 'status'" class="field">
        <span>Trạng thái mới</span>
        <select v-model="dlg.status" class="native-input">
          <option value="active">active</option>
          <option value="disabled">disabled</option>
        </select>
      </label>
      <template v-else>
        <label class="field">
          <span>Phòng ban đích (nhận dữ liệu)</span>
          <select v-model="dlg.targetCode" class="native-input">
            <option value="">—</option>
            <option v-for="opt in targetOptions" :key="opt.value" :value="opt.value" v-text="opt.label"></option>
          </select>
        </label>
        <label class="check-row">
          <input type="checkbox" v-model="dlg.moveUsers" />
          <span>Chuyển cả người dùng sang phòng đích</span>
        </label>
        <p class="muted-text">Dữ liệu (tài liệu) của phòng nguồn sẽ được gán sang phòng đích.</p>
      </template>
    </div>
    <template #footer>
      <Button label="Huỷ" severity="secondary" outlined :disabled="dlg.busy" @click="cancel" />
      <Button label="Lưu" :loading="dlg.busy" @click="submit" />
    </template>
  </Dialog>
</template>

<style scoped>
.stack-form {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.field > span {
  font-weight: 600;
  font-size: 0.85rem;
}
.check-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 0.5rem;
}
.native-input {
  padding: 0.45rem 0.55rem;
  border: 1px solid var(--p-inputtext-border-color, #cbd5e1);
  border-radius: 6px;
  background: var(--p-inputtext-background, #fff);
  font: inherit;
  box-sizing: border-box;
}
.muted-text {
  color: #64748b;
  font-size: 0.85rem;
}
</style>
