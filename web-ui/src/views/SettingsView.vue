<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { str } from "@/utils/rows";
import type { ApiRow, CreateForm, ResourceColumn, RowAction } from "@/types";

const columns: ResourceColumn[] = [
  { field: "key", header: "Khóa" },
  { field: "value", header: "Giá trị" },
];

type ExternalAiProfile = {
  provider: string;
  endpoint: string;
  default_model: string;
  secret_reference: string;
  allowed_surfaces: string[];
  retention_mode: string;
  policy_version: string;
  approved_by: string;
  risk_acceptance_ref: string;
  review_expires_at: string | null;
  review_state: "current" | "expired" | "unknown";
  is_active: boolean;
};

const policyProfiles = ref<ExternalAiProfile[]>([]);
const policyLoading = ref(true);
const policyError = ref("");

async function loadExternalAiPolicy() {
  policyLoading.value = true;
  policyError.value = "";
  try {
    const data = await apiGet<{ profiles: ExternalAiProfile[] }>("/api/settings/external-ai-policy");
    policyProfiles.value = data.profiles ?? [];
  } catch (err) {
    policyError.value = err instanceof Error ? err.message : "Không tải được external AI policy";
  } finally {
    policyLoading.value = false;
  }
}

function profileStatus(item: ExternalAiProfile): string {
  if (!item.is_active) return "Đã tắt";
  if (item.review_state === "expired") return "Cần review";
  return "Đang áp dụng";
}

const policyDialog = reactive({
  visible: false,
  busy: false,
  error: "",
  provider: "",
  endpoint: "",
  default_model: "",
  secret_reference: "",
  allowed_surfaces: "",
  retention_mode: "",
  policy_version: "",
  approved_by: "",
  risk_acceptance_ref: "",
  review_expires_at: "",
  is_active: true,
});

function openPolicyEdit(item: ExternalAiProfile) {
  policyDialog.provider = item.provider;
  policyDialog.endpoint = item.endpoint;
  policyDialog.default_model = item.default_model;
  policyDialog.secret_reference = item.secret_reference;
  policyDialog.allowed_surfaces = item.allowed_surfaces.join(", ");
  policyDialog.retention_mode = item.retention_mode;
  policyDialog.policy_version = item.policy_version;
  policyDialog.approved_by = item.approved_by;
  policyDialog.risk_acceptance_ref = item.risk_acceptance_ref;
  policyDialog.review_expires_at = String(item.review_expires_at ?? "").slice(0, 10);
  policyDialog.is_active = item.is_active;
  policyDialog.error = "";
  policyDialog.visible = true;
}

function surfaceList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

async function submitPolicyEdit() {
  policyDialog.busy = true;
  policyDialog.error = "";
  try {
    await apiSend(`/api/settings/external-ai-policy/${encodeURIComponent(policyDialog.provider)}`, "PUT", {
      endpoint: policyDialog.endpoint,
      default_model: policyDialog.default_model,
      secret_reference: policyDialog.secret_reference,
      allowed_surfaces: surfaceList(policyDialog.allowed_surfaces),
      retention_mode: policyDialog.retention_mode,
      policy_version: policyDialog.policy_version,
      approved_by: policyDialog.approved_by,
      risk_acceptance_ref: policyDialog.risk_acceptance_ref,
      review_expires_at: policyDialog.review_expires_at,
      is_active: policyDialog.is_active,
    });
    policyDialog.visible = false;
    await loadExternalAiPolicy();
  } catch (err) {
    policyDialog.error = err instanceof Error ? err.message : "Không lưu được external AI policy";
  } finally {
    policyDialog.busy = false;
  }
}

onMounted(loadExternalAiPolicy);

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ settings: Record<string, unknown> }>("/api/settings");
  const settings = data.settings ?? {};
  return Object.entries(settings).map(([key, value]) => ({ key, value }));
}

const createForm: CreateForm = {
  title: "Đặt cấu hình",
  triggerLabel: "Đặt khóa",
  fields: [
    { key: "key", label: "Khóa", required: true },
    { key: "value", label: "Giá trị", required: true },
  ],
  submit: (values) =>
    apiSend(`/api/settings/${encodeURIComponent(String(values.key))}`, "PUT", { value: values.value }),
};

// ---- Hop thoai sua gia tri (thay window.prompt) ----
const dlg = reactive({
  visible: false,
  busy: false,
  error: "",
  key: "",
  value: "",
});
let resolveDlg: (() => void) | null = null;
let rejectDlg: ((e: unknown) => void) | null = null;

function openEdit(row: ApiRow): Promise<void> {
  return new Promise((resolve, reject) => {
    resolveDlg = resolve;
    rejectDlg = reject;
    dlg.key = str(row, "key");
    dlg.value = str(row, "value");
    dlg.error = "";
    dlg.visible = true;
  });
}

async function submit() {
  dlg.busy = true;
  dlg.error = "";
  try {
    await apiSend(`/api/settings/${encodeURIComponent(dlg.key)}`, "PUT", { value: dlg.value });
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

const rowActions: RowAction[] = [{ label: "Sửa", run: (r) => openEdit(r) }];
</script>

<template>
  <Card class="policy-card">
    <template #title>External AI data policy</template>
    <template #subtitle>
      Chỉ hiển thị metadata policy và risk-acceptance; không hiển thị API key hay nội dung tài liệu.
    </template>
    <template #content>
      <Message v-if="policyError" severity="error" v-text="policyError"></Message>
      <div v-else-if="policyLoading" class="policy-loading"><ProgressSpinner /></div>
      <div v-else-if="policyProfiles.length" class="policy-table-wrap">
        <table class="policy-table">
          <thead>
            <tr>
              <th>Provider</th>
              <th>Model</th>
              <th>Surfaces</th>
              <th>Retention</th>
              <th>Policy</th>
              <th>Risk acceptance</th>
              <th>Review</th>
              <th>Trạng thái</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in policyProfiles" :key="item.provider">
              <td>{{ item.provider }}</td>
              <td>{{ item.default_model }}</td>
              <td>{{ item.allowed_surfaces.join(", ") }}</td>
              <td>{{ item.retention_mode }}</td>
              <td>{{ item.policy_version }}</td>
              <td>{{ item.risk_acceptance_ref }}</td>
              <td>{{ item.review_expires_at || "—" }}</td>
              <td :class="{ 'policy-attention': !item.is_active || item.review_state === 'expired' }">
                {{ profileStatus(item) }}
              </td>
              <td><Button label="Cập nhật" size="small" outlined @click="openPolicyEdit(item)" /></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="policy-muted">Chưa có provider profile. Chạy migration V0021 trước khi bật external AI.</p>
    </template>
  </Card>

  <ResourcePage
    title="Cấu hình"
    eyebrow="Settings"
    description="Cấu hình ứng dụng (AppSettings)."
    :columns="columns"
    :load="load"
    :create-form="createForm"
    :row-actions="rowActions"
  />

  <Dialog v-model:visible="dlg.visible" :header="`Sửa: ${dlg.key}`" modal :style="{ width: '480px' }" @hide="cancel">
    <div class="stack-form">
      <Message v-if="dlg.error" severity="error" v-text="dlg.error"></Message>
      <label class="field">
        <span>Giá trị mới</span>
        <textarea v-model="dlg.value" rows="4" class="native-input"></textarea>
      </label>
    </div>
    <template #footer>
      <Button label="Huỷ" severity="secondary" outlined :disabled="dlg.busy" @click="cancel" />
      <Button label="Lưu" :loading="dlg.busy" @click="submit" />
    </template>
  </Dialog>

  <Dialog v-model:visible="policyDialog.visible" header="Cập nhật external AI policy" modal :style="{ width: '620px' }">
    <form class="stack-form" @submit.prevent="submitPolicyEdit">
      <Message v-if="policyDialog.error" severity="error" v-text="policyDialog.error"></Message>
      <p class="policy-muted">Chỉ nhập secret reference như <code>env:VOYAGE_API_KEY</code>; không nhập API key vào đây.</p>
      <label class="field"><span>Provider</span><InputText v-model="policyDialog.provider" disabled /></label>
      <label class="field"><span>Endpoint</span><InputText v-model="policyDialog.endpoint" /></label>
      <label class="field"><span>Default model</span><InputText v-model="policyDialog.default_model" /></label>
      <label class="field"><span>Secret reference</span><InputText v-model="policyDialog.secret_reference" /></label>
      <label class="field"><span>Allowed surfaces, ngăn cách dấu phẩy</span><textarea v-model="policyDialog.allowed_surfaces" rows="2" class="native-input"></textarea></label>
      <label class="field"><span>Retention mode</span><InputText v-model="policyDialog.retention_mode" /></label>
      <label class="field"><span>Policy version</span><InputText v-model="policyDialog.policy_version" /></label>
      <label class="field"><span>Approved by</span><InputText v-model="policyDialog.approved_by" /></label>
      <label class="field"><span>Risk acceptance reference</span><InputText v-model="policyDialog.risk_acceptance_ref" /></label>
      <label class="field"><span>Review expiry</span><input v-model="policyDialog.review_expires_at" type="date" class="native-input" /></label>
      <label class="check-row"><input v-model="policyDialog.is_active" type="checkbox" /><span>Provider đang hoạt động</span></label>
      <div class="dialog-actions">
        <Button label="Huỷ" severity="secondary" outlined :disabled="policyDialog.busy" @click="policyDialog.visible = false" />
        <Button type="submit" label="Lưu policy" :loading="policyDialog.busy" />
      </div>
    </form>
  </Dialog>
</template>

<style scoped>
.policy-card {
  margin-bottom: 1rem;
}
.policy-loading {
  display: flex;
  justify-content: center;
  padding: 1rem;
}
.policy-table-wrap {
  overflow-x: auto;
}
.policy-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}
.policy-table th,
.policy-table td {
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--p-surface-200, #e5e7eb);
  text-align: left;
  vertical-align: top;
}
.policy-table th {
  color: var(--p-text-muted-color, #6b7280);
  font-weight: 600;
}
.policy-attention {
  color: var(--p-red-600, #dc2626);
  font-weight: 600;
}
.policy-muted {
  color: var(--p-text-muted-color, #6b7280);
}
.stack-form {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
}
.check-row,
.dialog-actions {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.dialog-actions {
  justify-content: flex-end;
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
.native-input {
  padding: 0.45rem 0.55rem;
  border: 1px solid var(--p-inputtext-border-color, #cbd5e1);
  border-radius: 6px;
  background: var(--p-inputtext-background, #fff);
  font: inherit;
  width: 100%;
  box-sizing: border-box;
}
</style>
