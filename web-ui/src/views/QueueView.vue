<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { num } from "@/utils/rows";
import type { ApiRow, ResourceColumn, ResourceFilter, RowAction, ToolbarAction } from "@/types";

// GD4: backend nay tra dict co ten cot (_rows_to_json) thay vi mang vi tri c0..cN.
const columns: ResourceColumn[] = [
  { field: "JobID", header: "JobID" },
  { field: "TenFile", header: "Tên file" },
  { field: "ThuMuc", header: "Phòng ban" },
  { field: "Status", header: "Trạng thái", kind: "tag" },
  { field: "ProgressPercent", header: "Tiến độ (%)" },
  { field: "Priority", header: "Ưu tiên" },
  { field: "UploadedBy", header: "Người tải" },
  { field: "CreatedAt", header: "Tạo lúc" },
  { field: "ErrorMessage", header: "Lỗi" },
];

const filters: ResourceFilter[] = [{ key: "status_value", label: "Lọc trạng thái", type: "text" }];

const eta = ref<number | null>(null);

async function load(f: Record<string, unknown>): Promise<ApiRow[]> {
  const data = await apiGet<{ jobs: ApiRow[] }>("/api/ingestion/jobs", f);
  return data.jobs ?? [];
}

async function loadEta() {
  try {
    const data = await apiGet<{ eta_seconds: number }>("/api/ingestion/eta");
    eta.value = data.eta_seconds ?? null;
  } catch {
    eta.value = null;
  }
}
onMounted(loadEta);

const rowActions: RowAction[] = [
  { label: "Huỷ", severity: "warning", run: (r) => apiSend(`/api/ingestion/jobs/${num(r, "JobID")}/cancel`, "POST") },
  { label: "Xếp lại", run: (r) => apiSend(`/api/ingestion/jobs/${num(r, "JobID")}/requeue`, "POST") },
  {
    label: "Ưu tiên cao",
    run: (r) => apiSend(`/api/ingestion/jobs/${num(r, "JobID")}/priority`, "PATCH", { priority: 1 }),
  },
  { label: "Chờ duyệt", run: (r) => apiSend(`/api/ingestion/jobs/${num(r, "JobID")}/pending-review`, "POST") },
  {
    label: "Xoá",
    severity: "danger",
    confirm: "Xoá job này?",
    run: (r) => apiSend(`/api/ingestion/jobs/${num(r, "JobID")}`, "DELETE"),
  },
];

// GD4: UI cho endpoint bulk-delete (trước đây chưa dùng). Chỉ admin (backend chan).
const bulk = reactive({ visible: false, busy: false, error: "", ids: "" });
let resolveBulk: (() => void) | null = null;
let rejectBulk: ((e: unknown) => void) | null = null;

function openBulk(): Promise<void> {
  return new Promise((resolve, reject) => {
    resolveBulk = resolve;
    rejectBulk = reject;
    bulk.ids = "";
    bulk.error = "";
    bulk.visible = true;
  });
}

async function submitBulk() {
  const ids = bulk.ids
    .split(/[\s,]+/)
    .map((x) => Number(x.trim()))
    .filter((n) => Number.isFinite(n) && n > 0);
  if (!ids.length) {
    bulk.error = "Nhập ít nhất một JobID.";
    return;
  }
  bulk.busy = true;
  bulk.error = "";
  try {
    await apiSend("/api/ingestion/jobs/bulk-delete", "POST", { ids });
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

function cancelBulk() {
  bulk.visible = false;
  rejectBulk?.(new Error(""));
  resolveBulk = null;
  rejectBulk = null;
}

const toolbar: ToolbarAction[] = [{ label: "Xoá nhiều job", severity: "danger", outlined: true, run: () => openBulk() }];
</script>

<template>
  <div>
    <ResourcePage
      title="Tiến trình ingest"
      eyebrow="Ingestion"
      :description="eta != null ? `Thời gian xử lý hàng đợi ước tính: ${eta}s` : 'Hàng đợi ingest hiện tại.'"
      :columns="columns"
      :filters="filters"
      :load="load"
      :row-actions="rowActions"
      :toolbar="toolbar"
    />

    <Dialog v-model:visible="bulk.visible" header="Xoá nhiều job" modal :style="{ width: '460px' }" @hide="cancelBulk">
      <div class="stack-form">
        <Message v-if="bulk.error" severity="error" v-text="bulk.error"></Message>
        <label class="field">
          <span>Danh sách JobID (cách nhau dấu phẩy hoặc khoảng trắng)</span>
          <textarea v-model="bulk.ids" rows="3" class="native-input" placeholder="ví dụ: 12, 15, 20"></textarea>
        </label>
        <p class="muted-text">Thao tác không thể hoàn tác; chỉ quản trị viên thực hiện được.</p>
      </div>
      <template #footer>
        <Button label="Huỷ" severity="secondary" outlined :disabled="bulk.busy" @click="cancelBulk" />
        <Button label="Xoá" severity="danger" :loading="bulk.busy" @click="submitBulk" />
      </template>
    </Dialog>
  </div>
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
.native-input {
  padding: 0.45rem 0.55rem;
  border: 1px solid var(--p-inputtext-border-color, #cbd5e1);
  border-radius: 6px;
  background: var(--p-inputtext-background, #fff);
  font: inherit;
  width: 100%;
  box-sizing: border-box;
}
.muted-text {
  color: #64748b;
  font-size: 0.85rem;
}
</style>
