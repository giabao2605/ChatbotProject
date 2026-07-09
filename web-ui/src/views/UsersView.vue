<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { pickStr, ROLES, SECURITY_LEVELS } from "@/utils/rows";
import type { ApiRow, ResourceColumn, RowAction, ToolbarAction } from "@/types";

type Opt = { label: string; value: string };
type EditMode = "create" | "roles" | "departments" | "sites" | "clearance" | "password";

const columns: ResourceColumn[] = [
  { field: "UserID", header: "ID" },
  { field: "Username", header: "Tài khoản" },
  { field: "DisplayName", header: "Tên hiển thị" },
  { field: "Department", header: "Phòng ban chính" },
  { field: "IsActive", header: "Kích hoạt", kind: "bool" },
  { field: "CreatedAt", header: "Ngày tạo" },
];

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ users: ApiRow[] }>("/api/users");
  return data.users ?? [];
}

function userId(row: ApiRow): number {
  const keys = Object.keys(row);
  const hit =
    keys.find((k) => k.toLowerCase() === "userid") ||
    keys.find((k) => k.toLowerCase() === "id") ||
    keys.find((k) => k.toLowerCase().includes("userid"));
  return Number(hit ? row[hit] : NaN);
}

// Danh muc phong ban / site -> dropdown (thay cho nhap tay CSV truoc day).
const deptOptions = ref<Opt[]>([]);
const siteOptions = ref<Opt[]>([]);
onMounted(async () => {
  try {
    const res = await apiGet<{ departments: ApiRow[] }>("/api/catalog/departments", { active_only: true });
    deptOptions.value = (res.departments ?? [])
      .map((r) => {
        const code = pickStr(r, ["code", "DeptCode"]);
        const name = pickStr(r, ["name", "DeptName"]);
        return { value: code, label: name ? `${code} — ${name}` : code };
      })
      .filter((d) => d.value);
  } catch {
    /* giu trong -> chi hien cac gia tri user dang co */
  }
  try {
    const res = await apiGet<{ sites: ApiRow[] }>("/api/catalog/sites", { active_only: true });
    siteOptions.value = (res.sites ?? [])
      .map((r) => {
        const code = pickStr(r, ["code", "SiteCode"]);
        const name = pickStr(r, ["name", "SiteName"]);
        return { value: code, label: name ? `${code} — ${name}` : code };
      })
      .filter((d) => d.value);
  } catch {
    /* giu trong */
  }
});

// ---- Hop thoai gop: tao user + sua vai tro / phong ban / site / muc mat / mat khau ----
const dlg = reactive({
  visible: false,
  busy: false,
  error: "",
  mode: "create" as EditMode,
  userId: 0,
  username: "",
});
const form = reactive({
  username: "",
  password: "",
  display_name: "",
  department: "",
});
const sel = reactive({
  roles: [] as string[],
  departments: [] as string[],
  sites: [] as string[],
  max_level: "public",
  password: "",
});
let originalRoles: string[] = [];
let resolveDlg: (() => void) | null = null;
let rejectDlg: ((e: unknown) => void) | null = null;

const DIALOG_TITLES: Record<EditMode, string> = {
  create: "Tạo người dùng",
  roles: "Đổi vai trò",
  departments: "Phòng ban",
  sites: "Site",
  clearance: "Mức mật",
  password: "Đổi mật khẩu",
};
const dialogTitle = computed(() =>
  dlg.mode === "create" ? DIALOG_TITLES.create : `${DIALOG_TITLES[dlg.mode]} — ${dlg.username}`,
);

// Gop danh muc voi cac gia tri user dang co (phong khi code khong con active).
const mergedDeptOptions = computed<Opt[]>(() => {
  const opts = [...deptOptions.value];
  const have = new Set(opts.map((o) => o.value));
  for (const d of sel.departments) if (d && !have.has(d)) opts.push({ value: d, label: d });
  return opts;
});
const mergedSiteOptions = computed<Opt[]>(() => {
  const opts = [...siteOptions.value];
  const have = new Set(opts.map((o) => o.value));
  for (const s of sel.sites) if (s && !have.has(s)) opts.push({ value: s, label: s });
  return opts;
});

function resetSelections() {
  sel.roles = [];
  sel.departments = [];
  sel.sites = [];
  sel.max_level = "public";
  sel.password = "";
  originalRoles = [];
}

function openCreate(): Promise<void> {
  return new Promise((resolve, reject) => {
    resolveDlg = resolve;
    rejectDlg = reject;
    dlg.mode = "create";
    dlg.userId = 0;
    dlg.username = "";
    dlg.error = "";
    form.username = "";
    form.password = "";
    form.display_name = "";
    form.department = "";
    resetSelections();
    dlg.visible = true;
  });
}

function openEdit(row: ApiRow, mode: Exclude<EditMode, "create">): Promise<void> {
  return new Promise((resolve, reject) => {
    resolveDlg = resolve;
    rejectDlg = reject;
    dlg.mode = mode;
    dlg.userId = userId(row);
    dlg.username = pickStr(row, ["Username", "username"]);
    dlg.error = "";
    resetSelections();
    dlg.visible = true;
    // Prefill gia tri hien tai tu GET /api/users/{id}.
    void prefill(dlg.userId);
  });
}

async function prefill(id: number) {
  try {
    const d = await apiGet<{
      roles?: string[];
      departments?: string[];
      clearance?: string;
      sites?: string[];
    }>(`/api/users/${id}`);
    sel.roles = [...(d.roles ?? [])];
    sel.departments = [...(d.departments ?? [])];
    sel.sites = [...(d.sites ?? [])];
    sel.max_level = d.clearance || "public";
    originalRoles = [...(d.roles ?? [])];
  } catch (err) {
    dlg.error = err instanceof Error ? err.message : "Không tải được quyền hiện tại";
  }
}

async function submit() {
  dlg.busy = true;
  dlg.error = "";
  try {
    if (dlg.mode === "create") {
      if (!form.username.trim()) throw new Error("Nhập tên đăng nhập");
      if (form.password.length < 8) throw new Error("Mật khẩu tối thiểu 8 ký tự");
      await apiSend("/api/users", "POST", {
        username: form.username.trim(),
        password: form.password,
        display_name: form.display_name || null,
        department: form.department || null,
        roles: [...sel.roles],
        departments: [...sel.departments],
      });
    } else if (dlg.mode === "roles") {
      const addRoles = sel.roles.filter((r) => !originalRoles.includes(r));
      const delRoles = originalRoles.filter((r) => !sel.roles.includes(r));
      await apiSend(`/api/users/${dlg.userId}/roles`, "PATCH", {
        is_active: true,
        add_roles: addRoles,
        del_roles: delRoles,
      });
    } else if (dlg.mode === "departments") {
      await apiSend(`/api/users/${dlg.userId}/departments`, "PATCH", { departments: [...sel.departments] });
    } else if (dlg.mode === "sites") {
      await apiSend(`/api/users/${dlg.userId}/sites`, "PATCH", { sites: [...sel.sites] });
    } else if (dlg.mode === "clearance") {
      await apiSend(`/api/users/${dlg.userId}/clearance`, "PATCH", { max_level: sel.max_level });
    } else if (dlg.mode === "password") {
      if (sel.password.length < 8) throw new Error("Mật khẩu tối thiểu 8 ký tự");
      await apiSend(`/api/users/${dlg.userId}/password`, "PATCH", { password: sel.password });
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

const toolbar: ToolbarAction[] = [{ label: "Tạo người dùng", run: () => openCreate() }];

const rowActions: RowAction[] = [
  { label: "Kích hoạt", run: (r) => apiSend(`/api/users/${userId(r)}/active`, "PATCH", { is_active: true }) },
  {
    label: "Vô hiệu",
    severity: "warning",
    run: (r) => apiSend(`/api/users/${userId(r)}/active`, "PATCH", { is_active: false }),
  },
  { label: "Đổi vai trò", run: (r) => openEdit(r, "roles") },
  { label: "Phòng ban", run: (r) => openEdit(r, "departments") },
  { label: "Site", run: (r) => openEdit(r, "sites") },
  { label: "Mức mật", run: (r) => openEdit(r, "clearance") },
  { label: "Đổi mật khẩu", run: (r) => openEdit(r, "password") },
  {
    label: "Xoá",
    severity: "danger",
    confirm: "Xoá người dùng này?",
    run: (r) => apiSend(`/api/users/${userId(r)}`, "DELETE"),
  },
];
</script>

<template>
  <ResourcePage
    title="Người dùng"
    eyebrow="Users"
    description="Quản lý tài khoản, vai trò, phòng ban, site và mức mật."
    :columns="columns"
    :load="load"
    :toolbar="toolbar"
    :row-actions="rowActions"
  />

  <Dialog v-model:visible="dlg.visible" :header="dialogTitle" modal :style="{ width: '460px' }" @hide="cancel">
    <div class="stack-form">
      <Message v-if="dlg.error" severity="error" v-text="dlg.error"></Message>

      <!-- Tạo người dùng -->
      <template v-if="dlg.mode === 'create'">
        <label class="field">
          <span>Tên đăng nhập *</span>
          <InputText v-model="form.username" />
        </label>
        <label class="field">
          <span>Mật khẩu (≥ 8 ký tự) *</span>
          <InputText v-model="form.password" type="password" />
        </label>
        <label class="field">
          <span>Tên hiển thị</span>
          <InputText v-model="form.display_name" />
        </label>
        <label class="field">
          <span>Phòng ban chính</span>
          <select v-model="form.department" class="native-select">
            <option value="">—</option>
            <option v-for="opt in mergedDeptOptions" :key="opt.value" :value="opt.value" v-text="opt.label"></option>
          </select>
        </label>
        <div class="field">
          <span>Vai trò</span>
          <label v-for="opt in ROLES" :key="opt.value" class="check-row">
            <input type="checkbox" :value="opt.value" v-model="sel.roles" />
            <span v-text="opt.label"></span>
          </label>
        </div>
        <div class="field">
          <span>Phòng ban</span>
          <label v-for="opt in mergedDeptOptions" :key="opt.value" class="check-row">
            <input type="checkbox" :value="opt.value" v-model="sel.departments" />
            <span v-text="opt.label"></span>
          </label>
          <p v-if="!mergedDeptOptions.length" class="muted-text">Chưa có danh mục phòng ban.</p>
        </div>
      </template>

      <!-- Đổi vai trò -->
      <div v-else-if="dlg.mode === 'roles'" class="field">
        <span>Chọn vai trò cho tài khoản</span>
        <label v-for="opt in ROLES" :key="opt.value" class="check-row">
          <input type="checkbox" :value="opt.value" v-model="sel.roles" />
          <span v-text="opt.label"></span>
        </label>
      </div>

      <!-- Phòng ban -->
      <div v-else-if="dlg.mode === 'departments'" class="field">
        <span>Phòng ban được phép</span>
        <label v-for="opt in mergedDeptOptions" :key="opt.value" class="check-row">
          <input type="checkbox" :value="opt.value" v-model="sel.departments" />
          <span v-text="opt.label"></span>
        </label>
        <p v-if="!mergedDeptOptions.length" class="muted-text">Chưa có danh mục phòng ban.</p>
      </div>

      <!-- Site -->
      <div v-else-if="dlg.mode === 'sites'" class="field">
        <span>Site được phép (để trống = không giới hạn)</span>
        <label v-for="opt in mergedSiteOptions" :key="opt.value" class="check-row">
          <input type="checkbox" :value="opt.value" v-model="sel.sites" />
          <span v-text="opt.label"></span>
        </label>
        <p v-if="!mergedSiteOptions.length" class="muted-text">Chưa có danh mục site.</p>
      </div>

      <!-- Mức mật -->
      <label v-else-if="dlg.mode === 'clearance'" class="field">
        <span>Mức mật tối đa</span>
        <select v-model="sel.max_level" class="native-select">
          <option v-for="opt in SECURITY_LEVELS" :key="opt.value" :value="opt.value" v-text="opt.label"></option>
        </select>
      </label>

      <!-- Đổi mật khẩu -->
      <label v-else-if="dlg.mode === 'password'" class="field">
        <span>Mật khẩu mới (≥ 8 ký tự)</span>
        <InputText v-model="sel.password" type="password" />
      </label>
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
  font-weight: 400;
}
.native-select {
  padding: 0.4rem 0.5rem;
  border: 1px solid var(--p-inputtext-border-color, #cbd5e1);
  border-radius: 6px;
  background: var(--p-inputtext-background, #fff);
}
.muted-text {
  color: #94a3b8;
  font-size: 0.85rem;
}
</style>
