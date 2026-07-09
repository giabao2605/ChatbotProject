<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { pickStr } from "@/utils/rows";
import type { ApiRow, CreateForm, RowAction } from "@/types";

type Opt = { label: string; value: string };

async function loadDepartments(): Promise<ApiRow[]> {
  const data = await apiGet<{ departments: ApiRow[] }>("/api/catalog/departments", { active_only: false });
  return data.departments ?? [];
}

async function loadSites(): Promise<ApiRow[]> {
  const data = await apiGet<{ sites: ApiRow[] }>("/api/catalog/sites", { active_only: false });
  return data.sites ?? [];
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
