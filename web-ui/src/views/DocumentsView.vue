<script setup lang="ts">
import { reactive, ref } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { num, pickStr, SECURITY_LEVELS } from "@/utils/rows";
import type { ApiRow, ResourceColumn, ResourceFilter, RowAction, ToolbarAction } from "@/types";

const columns: ResourceColumn[] = [
  { field: "DocID", header: "DocID" },
  { field: "OriginalFileName", header: "Tên file" },
  { field: "Department", header: "Phòng ban" },
  { field: "Domain", header: "Domain" },
  { field: "SecurityLevel", header: "Mức mật", kind: "tag" },
  { field: "Site", header: "Site" },
  { field: "VersionNo", header: "Phiên bản" },
  { field: "IsCurrentVersion", header: "Hiện hành", kind: "bool" },
  { field: "LifecycleStatus", header: "Vòng đời", kind: "tag" },
  { field: "ReviewStatus", header: "Duyệt", kind: "tag" },
  { field: "CreatedAt", header: "Ngày tải" },
];

const filters: ResourceFilter[] = [
  { key: "search", label: "Tìm kiếm", type: "text" },
  { key: "dept", label: "Phòng ban", type: "text" },
  { key: "domain", label: "Domain", type: "text" },
  { key: "sec", label: "Mức mật", type: "select", options: SECURITY_LEVELS },
  {
    key: "eff_mode",
    label: "Hiệu lực",
    type: "select",
    options: [
      { label: "Còn hiệu lực", value: "con" },
      { label: "Sắp hết hạn", value: "sap" },
      { label: "Đã hết hạn", value: "het" },
    ],
  },
];

async function load(f: Record<string, unknown>): Promise<ApiRow[]> {
  const data = await apiGet<{ documents: ApiRow[] }>("/api/documents", f);
  return data.documents ?? [];
}

// GD2: form sua metadata day du (~13 truong) thay cho chuoi window.prompt cu.
type MetaFieldType = "text" | "textarea" | "date" | "lang" | "status";
const META_FIELDS: ReadonlyArray<{ key: string; label: string; type: MetaFieldType }> = [
  { key: "title", label: "Tiêu đề", type: "text" },
  { key: "summary", label: "Tóm tắt", type: "textarea" },
  { key: "tags", label: "Tags", type: "text" },
  { key: "doc_number", label: "Số hiệu", type: "text" },
  { key: "issued_date", label: "Ngày ban hành", type: "date" },
  { key: "effective_date", label: "Ngày hiệu lực", type: "date" },
  { key: "expiry_date", label: "Ngày hết hạn", type: "date" },
  { key: "review_date", label: "Ngày rà soát", type: "date" },
  { key: "owner_signer", label: "Người ký / chủ quản", type: "text" },
  { key: "site", label: "Site", type: "text" },
  { key: "language", label: "Ngôn ngữ", type: "lang" },
  { key: "effective_status", label: "Trạng thái hiệu lực", type: "status" },
];
const LANGUAGES = ["vi", "en", "ja", "zh", "ko"];
const EFFECTIVE_STATUSES = ["draft", "effective", "superseded", "expired", "withdrawn"];

const editVisible = ref(false);
const editBusy = ref(false);
const editError = ref("");
const editDocId = ref<number | null>(null);
const editValues = reactive<Record<string, string>>({});
let resolveEdit: (() => void) | null = null;
let rejectEdit: ((e: unknown) => void) | null = null;

function toDateStr(value: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(value);
  return m ? m[1] : "";
}

function openEdit(row: ApiRow): Promise<void> {
  return new Promise((resolve, reject) => {
    resolveEdit = resolve;
    rejectEdit = reject;
    editDocId.value = num(row, "DocID");
    editError.value = "";
    for (const f of META_FIELDS) {
      let val = pickStr(row, [f.key, f.key.replace(/_/g, "")]);
      if (f.type === "date") val = toDateStr(val);
      editValues[f.key] = val;
    }
    editVisible.value = true;
  });
}

async function submitEdit() {
  if (editDocId.value === null) return;
  editBusy.value = true;
  editError.value = "";
  try {
    const body: Record<string, unknown> = {};
    for (const f of META_FIELDS) {
      const v = (editValues[f.key] ?? "").trim();
      if (v) body[f.key] = v; // de trong = giu nguyen
    }
    if (!Object.keys(body).length) {
      editError.value = "Chưa nhập trường nào để cập nhật.";
      editBusy.value = false;
      return;
    }
    await apiSend(`/api/documents/${editDocId.value}/metadata`, "PATCH", body);
    editVisible.value = false;
    resolveEdit?.();
    resolveEdit = null;
    rejectEdit = null;
  } catch (err) {
    editError.value = err instanceof Error ? err.message : "Lỗi";
  } finally {
    editBusy.value = false;
  }
}

function cancelEdit() {
  editVisible.value = false;
  rejectEdit?.(new Error(""));
  resolveEdit = null;
  rejectEdit = null;
}

const rowActions: RowAction[] = [
  {
    label: "Xem trang",
    run: async (row) => {
      window.open(`/api/files/documents/${num(row, "DocID")}/pages/1`, "_blank");
    },
  },
  {
    label: "Tải bản gốc",
    run: async (row) => {
      window.open(`/api/files/documents/${num(row, "DocID")}/original`, "_blank");
    },
  },
  {
    label: "Sửa metadata",
    run: (row) => openEdit(row),
  },
  {
    label: "Đặt hiện hành",
    visible: (row) => !num(row, "IsCurrentVersion"),
    run: (row) => apiSend(`/api/documents/${num(row, "DocID")}/current`, "PATCH"),
  },
  {
    label: "Đánh dấu hết hạn",
    severity: "warning",
    confirm: "Đánh dấu tài liệu này hết hiệu lực?",
    run: (row) => apiSend(`/api/documents/${num(row, "DocID")}/expired`, "PATCH"),
  },
  {
    label: "Xoá",
    severity: "danger",
    confirm: "Xoá vĩnh viễn tài liệu này?",
    run: (row) => apiSend(`/api/documents/${num(row, "DocID")}`, "DELETE"),
  },
];

const bulk = reactive({ visible: false, busy: false, error: "", ids: "" });
let resolveBulk: (() => void) | null = null;
let rejectBulk: ((e: unknown) => void) | null = null;

function openBulkDelete(): Promise<void> {
  return new Promise((resolve, reject) => {
    resolveBulk = resolve;
    rejectBulk = reject;
    bulk.ids = "";
    bulk.error = "";
    bulk.visible = true;
  });
}

async function submitBulkDelete() {
  const ids = bulk.ids
    .split(/[\s,]+/)
    .map((x) => Number(x.trim()))
    .filter((n) => Number.isFinite(n) && n > 0);
  if (!ids.length) {
    bulk.error = "Nhập ít nhất một DocID.";
    return;
  }
  bulk.busy = true;
  bulk.error = "";
  try {
    for (const id of ids) {
      await apiSend(`/api/documents/${id}`, "DELETE");
    }
    bulk.visible = false;
    resolveBulk?.();
    resolveBulk = null;
    rejectBulk = null;
  } catch (err) {
    bulk.error = err instanceof Error ? err.message : "Lỗi";
  } finally {
    bulk.busy = false;
  }
}

function cancelBulkDelete() {
  bulk.visible = false;
  rejectBulk?.(new Error(""));
  resolveBulk = null;
  rejectBulk = null;
}

const toolbar: ToolbarAction[] = [
  { label: "Xóa nhiều DocID", severity: "danger", outlined: true, run: () => openBulkDelete() },
];
</script>

<template>
  <ResourcePage
    title="Kho tài liệu"
    eyebrow="Documents"
    description="Danh sách tài liệu đã xuất bản theo quyền truy cập của bạn."
    :columns="columns"
    :filters="filters"
    :load="load"
    :row-actions="rowActions"
    :toolbar="toolbar"
  />

  <Dialog v-model:visible="editVisible" header="Sửa metadata tài liệu" modal :style="{ width: '560px' }">
    <Message v-if="editError" severity="error" v-text="editError"></Message>
    <p class="muted-text" style="margin-top: 0">Để trống = giữ nguyên giá trị hiện tại.</p>
    <form class="stack-form" @submit.prevent="submitEdit">
      <label v-for="f in META_FIELDS" :key="f.key" class="stack-field">
        <span v-text="f.label"></span>
        <textarea
          v-if="f.type === 'textarea'"
          v-model="editValues[f.key]"
          rows="3"
          class="native-select"
        ></textarea>
        <input v-else-if="f.type === 'date'" type="date" v-model="editValues[f.key]" class="native-select" />
        <template v-else-if="f.type === 'lang'">
          <input v-model="editValues[f.key]" list="doc-langs" class="native-select" />
          <datalist id="doc-langs">
            <option v-for="l in LANGUAGES" :key="l" :value="l"></option>
          </datalist>
        </template>
        <template v-else-if="f.type === 'status'">
          <input v-model="editValues[f.key]" list="doc-statuses" class="native-select" />
          <datalist id="doc-statuses">
            <option v-for="s in EFFECTIVE_STATUSES" :key="s" :value="s"></option>
          </datalist>
        </template>
        <InputText v-else v-model="editValues[f.key]" />
      </label>
      <div class="form-actions">
        <Button type="button" label="Huỷ" severity="secondary" outlined :disabled="editBusy" @click="cancelEdit" />
        <Button type="submit" label="Lưu" :loading="editBusy" />
      </div>
    </form>
  </Dialog>

  <Dialog v-model:visible="bulk.visible" header="Xóa nhiều tài liệu" modal :style="{ width: '460px' }" @hide="cancelBulkDelete">
    <div class="stack-form">
      <Message v-if="bulk.error" severity="error" v-text="bulk.error"></Message>
      <label class="stack-field">
        <span>Danh sách DocID</span>
        <textarea v-model="bulk.ids" rows="3" class="native-select" placeholder="ví dụ: 12, 15, 20"></textarea>
      </label>
      <p class="muted-text">Thao tác xóa vĩnh viễn tài liệu và vector liên quan.</p>
    </div>
    <template #footer>
      <Button label="Huỷ" severity="secondary" outlined :disabled="bulk.busy" @click="cancelBulkDelete" />
      <Button label="Xóa" severity="danger" :loading="bulk.busy" @click="submitBulkDelete" />
    </template>
  </Dialog>
</template>
