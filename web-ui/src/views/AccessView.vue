<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { num, pickStr, SECURITY_LEVELS } from "@/utils/rows";
import { useAuthStore } from "@/stores/auth";
import type { ApiRow, CreateForm, ResourceColumn, RowAction } from "@/types";

const auth = useAuthStore();
const canReview = computed(
  () => auth.user?.roles.includes("admin") || auth.user?.roles.includes("reviewer"),
);
const canAdmin = computed(() => !!auth.user?.roles.includes("admin"));

// Danh sach phong ban active -> dropdown cho o "Phong ban mong muon".
const deptOptions = ref<Array<{ label: string; value: string }>>([]);
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
    // Neu catalog loi thi giu o nhap tay (fallback ben duoi).
  }
});

async function loadMine(): Promise<ApiRow[]> {
  const data = await apiGet<{ requests: ApiRow[] }>("/api/access/my-requests");
  return data.requests ?? [];
}

async function loadPending(): Promise<ApiRow[]> {
  const data = await apiGet<{ requests: ApiRow[] }>("/api/access/requests", { status_value: "pending" });
  return data.requests ?? [];
}

async function loadHistory(): Promise<ApiRow[]> {
  const data = await apiGet<{ requests: ApiRow[] }>("/api/access/requests", { status_value: "all", limit: 200 });
  return data.requests ?? [];
}

async function loadGrants(): Promise<ApiRow[]> {
  const data = await apiGet<{ grants: ApiRow[] }>("/api/access/grants", { limit: 100 });
  return data.grants ?? [];
}

async function loadUsers(): Promise<ApiRow[]> {
  const data = await apiGet<{ users: ApiRow[] }>("/api/access/users");
  return (data.users ?? []).map((u) => ({
    ...u,
    departments_text: Array.isArray(u.departments) ? (u.departments as string[]).join(", ") : "",
  }));
}

const createForm = computed<CreateForm>(() => ({
  title: "Tạo yêu cầu quyền",
  fields: [
    {
      key: "request_type",
      label: "Loại yêu cầu",
      type: "select",
      required: true,
      options: [
        { label: "Nâng mức mật", value: "clearance" },
        { label: "Thêm phòng ban", value: "department" },
      ],
    },
    { key: "requested_level", label: "Mức mật mong muốn", type: "select", options: SECURITY_LEVELS },
    {
      key: "requested_dept",
      label: "Phòng ban mong muốn",
      type: deptOptions.value.length ? "select" : "text",
      options: deptOptions.value,
    },
    { key: "reason", label: "Lý do", type: "textarea" },
  ],
  submit: (values) => apiSend("/api/access/request", "POST", values),
}));

function reqId(row: ApiRow): number {
  const keys = Object.keys(row);
  const hit = keys.find((k) => k.toLowerCase().includes("requestid")) || keys.find((k) => k.toLowerCase() === "id");
  return Number(hit ? row[hit] : num(row, "request_id"));
}

const reviewActions: RowAction[] = [
  {
    label: "Duyệt",
    run: (r) => apiSend(`/api/access/requests/${reqId(r)}/resolve`, "POST", { decision: "approved" }),
  },
  {
    label: "Từ chối",
    severity: "warning",
    run: (r) => apiSend(`/api/access/requests/${reqId(r)}/resolve`, "POST", { decision: "rejected" }),
  },
];

const historyColumns: ResourceColumn[] = [
  { field: "request_id", header: "ID" },
  { field: "username", header: "Người yêu cầu" },
  { field: "request_type", header: "Loại" },
  { field: "requested_level", header: "Mức mật", kind: "tag" },
  { field: "requested_dept", header: "Phòng ban" },
  { field: "status", header: "Trạng thái", kind: "tag" },
  { field: "reviewer_username", header: "Người duyệt" },
  { field: "created_at", header: "Ngày tạo" },
];

const grantColumns: ResourceColumn[] = [
  { field: "created_at", header: "Thời điểm" },
  { field: "username", header: "Người dùng" },
  { field: "action", header: "Hành động", kind: "tag" },
  { field: "entity_type", header: "Đối tượng" },
  { field: "entity_id", header: "ID" },
  { field: "details", header: "Chi tiết" },
];

const userColumns: ResourceColumn[] = [
  { field: "username", header: "Tài khoản" },
  { field: "display_name", header: "Tên" },
  { field: "max_level", header: "Mức mật", kind: "tag" },
  { field: "departments_text", header: "Phòng ban" },
  { field: "is_active", header: "Kích hoạt", kind: "bool" },
];

// GD2: hop thoai thu hoi muc mat / phong ban.
const revoke = reactive({
  visible: false,
  busy: false,
  error: "",
  mode: "clearance" as "clearance" | "department",
  userId: 0,
  username: "",
  newLevel: "public",
  department: "",
  deptOptions: [] as string[],
  reason: "",
});
let resolveRevoke: (() => void) | null = null;
let rejectRevoke: ((e: unknown) => void) | null = null;

function openRevoke(row: ApiRow, mode: "clearance" | "department"): Promise<void> {
  return new Promise((resolve, reject) => {
    resolveRevoke = resolve;
    rejectRevoke = reject;
    revoke.mode = mode;
    revoke.userId = num(row, "user_id");
    revoke.username = String(row.username ?? "");
    revoke.error = "";
    revoke.reason = "";
    revoke.newLevel = String(row.max_level ?? "public");
    revoke.deptOptions = Array.isArray(row.departments) ? (row.departments as string[]) : [];
    revoke.department = revoke.deptOptions[0] ?? "";
    revoke.visible = true;
  });
}

async function submitRevoke() {
  revoke.busy = true;
  revoke.error = "";
  try {
    if (revoke.mode === "clearance") {
      await apiSend(`/api/access/users/${revoke.userId}/revoke-clearance`, "POST", {
        new_level: revoke.newLevel,
        reason: revoke.reason,
      });
    } else {
      if (!revoke.department) {
        revoke.error = "Chọn phòng ban cần thu hồi.";
        revoke.busy = false;
        return;
      }
      await apiSend(`/api/access/users/${revoke.userId}/revoke-department`, "POST", {
        department: revoke.department,
        reason: revoke.reason,
      });
    }
    revoke.visible = false;
    resolveRevoke?.();
    resolveRevoke = null;
    rejectRevoke = null;
  } catch (err) {
    revoke.error = err instanceof Error ? err.message : "Lỗi";
  } finally {
    revoke.busy = false;
  }
}

function cancelRevoke() {
  revoke.visible = false;
  rejectRevoke?.(new Error(""));
  resolveRevoke = null;
  rejectRevoke = null;
}

const userActions: RowAction[] = [
  { label: "Thu hồi mức mật", severity: "warning", run: (r) => openRevoke(r, "clearance") },
  { label: "Thu hồi phòng ban", severity: "warning", run: (r) => openRevoke(r, "department") },
];
</script>

<template>
  <div class="stacked-pages">
    <ResourcePage
      title="Yêu cầu quyền của tôi"
      eyebrow="Access"
      description="Gửi yêu cầu nâng mức mật hoặc thêm phòng ban."
      :load="loadMine"
      :create-form="createForm"
    />
    <ResourcePage
      v-if="canReview"
      title="Yêu cầu chờ duyệt"
      eyebrow="Access review"
      description="Duyệt hoặc từ chối các yêu cầu đang chờ."
      :load="loadPending"
      :row-actions="reviewActions"
    />
    <ResourcePage
      v-if="canReview"
      title="Lịch sử yêu cầu"
      eyebrow="Access history"
      description="Toàn bộ yêu cầu đã xử lý (đã duyệt/từ chối) và đang chờ."
      :load="loadHistory"
      :columns="historyColumns"
    />
    <ResourcePage
      v-if="canAdmin"
      title="Lịch sử cấp quyền"
      eyebrow="Grants"
      description="Nhật ký cấp/thu hồi quyền (từ AuditLog)."
      :load="loadGrants"
      :columns="grantColumns"
    />
    <ResourcePage
      v-if="canAdmin"
      title="Quản lý quyền người dùng"
      eyebrow="User access"
      description="Xem mức mật + phòng ban của từng người dùng; thu hồi khi cần."
      :load="loadUsers"
      :columns="userColumns"
      :row-actions="userActions"
    />

    <Dialog
      v-model:visible="revoke.visible"
      :header="revoke.mode === 'clearance' ? `Thu hồi mức mật — ${revoke.username}` : `Thu hồi phòng ban — ${revoke.username}`"
      modal
      :style="{ width: '460px' }"
    >
      <Message v-if="revoke.error" severity="error" v-text="revoke.error"></Message>
      <form class="stack-form" @submit.prevent="submitRevoke">
        <label v-if="revoke.mode === 'clearance'" class="stack-field">
          <span>Hạ mức mật xuống</span>
          <select v-model="revoke.newLevel" class="native-select">
            <option v-for="opt in SECURITY_LEVELS" :key="opt.value" :value="opt.value" v-text="opt.label"></option>
          </select>
        </label>
        <label v-else class="stack-field">
          <span>Phòng ban thu hồi</span>
          <select v-model="revoke.department" class="native-select">
            <option value="" disabled>— Chọn phòng ban —</option>
            <option v-for="d in revoke.deptOptions" :key="d" :value="d" v-text="d"></option>
          </select>
          <small v-if="!revoke.deptOptions.length" class="muted-text">Người dùng này chưa được gán phòng ban nào.</small>
        </label>
        <label class="stack-field">
          <span>Lý do (tuỳ chọn)</span>
          <textarea v-model="revoke.reason" rows="2" class="native-select"></textarea>
        </label>
        <div class="form-actions">
          <Button type="button" label="Huỷ" severity="secondary" outlined :disabled="revoke.busy" @click="cancelRevoke" />
          <Button type="submit" label="Thu hồi" severity="danger" :loading="revoke.busy" />
        </div>
      </form>
    </Dialog>
  </div>
</template>
