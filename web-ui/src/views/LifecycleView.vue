<script setup lang="ts">
import { computed, reactive } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { num, str } from "@/utils/rows";
import type { ApiRow, ResourceColumn, RowAction, ToolbarAction } from "@/types";

type LifecyclePayload = {
  expired?: ApiRow[];
  expiring_soon?: ApiRow[];
  needs_review?: ApiRow[];
  counts?: Record<string, number>;
};

// Backend tra ve dict long nhau {expired, expiring_soon, needs_review, counts}
// (xem get_lifecycle_overview). Tach thanh 3 bang de hien thi + thao tac.
async function loadGroup(key: "expired" | "expiring_soon" | "needs_review"): Promise<ApiRow[]> {
  const data = await apiGet<LifecyclePayload>("/api/lifecycle", { soon_days: 30 });
  return data[key] ?? [];
}

const loadExpired = () => loadGroup("expired");
const loadExpiringSoon = () => loadGroup("expiring_soon");
const loadNeedsReview = () => loadGroup("needs_review");

const columns: ResourceColumn[] = [
  { field: "doc_id", header: "DocID" },
  { field: "file", header: "Tên file" },
  { field: "dept", header: "Phòng ban" },
  { field: "version_no", header: "Phiên bản" },
  { field: "effective_status", header: "Trạng thái", kind: "tag" },
  { field: "effective_date", header: "Hiệu lực từ" },
  { field: "expiry_date", header: "Hết hạn" },
  { field: "review_date", header: "Rà soát" },
  { field: "last_reviewed_at", header: "Rà soát lần cuối" },
];

// ---- Hop thoai (thay window.prompt): "reviewed" (so ngay) + "dates" (date picker) ----
const dlg = reactive({
  visible: false,
  busy: false,
  error: "",
  mode: "reviewed" as "reviewed" | "dates",
  docId: 0,
  file: "",
  next_review_days: 180,
  effective_date: "",
  expiry_date: "",
  review_date: "",
});
let resolveDlg: (() => void) | null = null;
let rejectDlg: ((e: unknown) => void) | null = null;

const dialogTitle = computed(() =>
  dlg.mode === "reviewed" ? `Đã rà soát — ${dlg.file}` : `Đặt ngày — ${dlg.file}`,
);

// Cat phan ngay YYYY-MM-DD tu chuoi datetime de gan vao input type=date.
function dateOnly(row: ApiRow, key: string): string {
  const m = str(row, key).match(/^(\d{4}-\d{2}-\d{2})/);
  return m ? m[1] : "";
}

function openDialog(row: ApiRow, mode: "reviewed" | "dates"): Promise<void> {
  return new Promise((resolve, reject) => {
    resolveDlg = resolve;
    rejectDlg = reject;
    dlg.mode = mode;
    dlg.docId = num(row, "doc_id");
    dlg.file = str(row, "file");
    dlg.error = "";
    dlg.next_review_days = 180;
    dlg.effective_date = dateOnly(row, "effective_date");
    dlg.expiry_date = dateOnly(row, "expiry_date");
    dlg.review_date = dateOnly(row, "review_date");
    dlg.visible = true;
  });
}

async function submit() {
  dlg.busy = true;
  dlg.error = "";
  try {
    if (dlg.mode === "reviewed") {
      await apiSend(`/api/lifecycle/documents/${dlg.docId}/reviewed`, "POST", {
        next_review_days: Number(dlg.next_review_days) || 180,
      });
    } else {
      const body: Record<string, unknown> = {};
      if (dlg.effective_date) body.effective_date = dlg.effective_date;
      if (dlg.expiry_date) body.expiry_date = dlg.expiry_date;
      if (dlg.review_date) body.review_date = dlg.review_date;
      if (!Object.keys(body).length) {
        dlg.error = "Nhập ít nhất một ngày.";
        dlg.busy = false;
        return;
      }
      await apiSend(`/api/lifecycle/documents/${dlg.docId}`, "PATCH", body);
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

const rowActions: RowAction[] = [
  { label: "Đã rà soát", run: (r) => openDialog(r, "reviewed") },
  { label: "Đặt ngày", run: (r) => openDialog(r, "dates") },
];

const toolbar: ToolbarAction[] = [
  {
    label: "Cập nhật hết hạn",
    confirm: "Đánh dấu các tài liệu quá hạn?",
    run: () => apiSend("/api/lifecycle/refresh-expired", "POST"),
  },
];
</script>

<template>
  <div class="stacked-pages">
    <ResourcePage
      title="Đã hết hạn"
      eyebrow="Lifecycle"
      description="Tài liệu hiện hành đã quá hạn hiệu lực."
      :columns="columns"
      :load="loadExpired"
      :row-actions="rowActions"
      :toolbar="toolbar"
    />
    <ResourcePage
      title="Sắp hết hạn"
      eyebrow="Lifecycle"
      description="Tài liệu sắp hết hạn trong 30 ngày tới."
      :columns="columns"
      :load="loadExpiringSoon"
      :row-actions="rowActions"
    />
    <ResourcePage
      title="Cần rà soát"
      eyebrow="Lifecycle"
      description="Tài liệu đến hạn rà soát định kỳ."
      :columns="columns"
      :load="loadNeedsReview"
      :row-actions="rowActions"
    />
  </div>

  <Dialog v-model:visible="dlg.visible" :header="dialogTitle" modal :style="{ width: '440px' }" @hide="cancel">
    <div class="stack-form">
      <Message v-if="dlg.error" severity="error" v-text="dlg.error"></Message>
      <label v-if="dlg.mode === 'reviewed'" class="field">
        <span>Chu kỳ rà soát tiếp theo (số ngày)</span>
        <input v-model.number="dlg.next_review_days" type="number" min="1" class="native-input" />
      </label>
      <template v-else>
        <label class="field">
          <span>Ngày hiệu lực</span>
          <input v-model="dlg.effective_date" type="date" class="native-input" />
        </label>
        <label class="field">
          <span>Ngày hết hạn</span>
          <input v-model="dlg.expiry_date" type="date" class="native-input" />
        </label>
        <label class="field">
          <span>Ngày rà soát</span>
          <input v-model="dlg.review_date" type="date" class="native-input" />
        </label>
        <p class="muted-text">Để trống = giữ nguyên.</p>
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
