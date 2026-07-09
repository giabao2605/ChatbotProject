<script setup lang="ts">
import { reactive } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { str } from "@/utils/rows";
import type { ApiRow, CreateForm, ResourceColumn, RowAction } from "@/types";

const columns: ResourceColumn[] = [
  { field: "key", header: "Khóa" },
  { field: "value", header: "Giá trị" },
];

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
</style>
