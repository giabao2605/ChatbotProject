<script setup lang="ts">
import { ref } from "vue";
import ResourcePage from "@/components/ResourcePage.vue";
import { apiGet, apiSend } from "@/api/client";
import { pickNum } from "@/utils/rows";
import type { ApiRow, CreateForm, ResourceColumn, RowAction } from "@/types";

type Syn = { synonym_id: number; synonym: string; is_active: boolean };

const columns: ResourceColumn[] = [
  { field: "code", header: "Mã vật tư" },
  { field: "display", header: "Tên hiển thị" },
  { field: "category", header: "Nhóm" },
  { field: "is_active", header: "Kích hoạt", kind: "bool" },
  { field: "synonyms_text", header: "Đồng nghĩa" },
];

function synonymsOf(row: ApiRow): Syn[] {
  return Array.isArray(row.synonyms) ? (row.synonyms as Syn[]) : [];
}

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ materials: ApiRow[] }>("/api/materials");
  return (data.materials ?? []).map((m) => ({
    ...m,
    synonyms_text: synonymsOf(m)
      .map((s) => s.synonym)
      .join(", "),
  }));
}

const createForm: CreateForm = {
  title: "Thêm vật tư",
  fields: [
    { key: "code", label: "Mã vật tư", required: true },
    { key: "display", label: "Tên hiển thị", required: true },
    { key: "category", label: "Nhóm" },
  ],
  submit: (values) => apiSend("/api/materials", "POST", values),
};

function mid(row: ApiRow): number {
  return pickNum(row, ["material_id", "MaterialID", "id"]);
}

// GD2: hop thoai quan ly tu dong nghia (xem + them + xoa tung tu).
const synVisible = ref(false);
const synBusy = ref(false);
const synError = ref("");
const synMatId = ref<number | null>(null);
const synMatName = ref("");
const synList = ref<Syn[]>([]);
const newSyn = ref("");
let resolveSyn: (() => void) | null = null;

async function reloadSyns() {
  const data = await apiGet<{ materials: ApiRow[] }>("/api/materials");
  const m = (data.materials ?? []).find((x) => Number((x as ApiRow).material_id) === synMatId.value);
  synList.value = m ? synonymsOf(m) : [];
}

function openSyn(row: ApiRow): Promise<void> {
  return new Promise((resolve) => {
    resolveSyn = resolve;
    synMatId.value = mid(row);
    synMatName.value = String(row.display ?? row.code ?? "");
    synList.value = synonymsOf(row);
    newSyn.value = "";
    synError.value = "";
    synVisible.value = true;
  });
}

async function addSyn() {
  const s = newSyn.value.trim();
  if (!s || synMatId.value === null) return;
  synBusy.value = true;
  synError.value = "";
  try {
    await apiSend(`/api/materials/${synMatId.value}/synonyms`, "POST", { synonym: s });
    newSyn.value = "";
    await reloadSyns();
  } catch (err) {
    synError.value = err instanceof Error ? err.message : "Lỗi";
  } finally {
    synBusy.value = false;
  }
}

async function removeSyn(s: Syn) {
  synBusy.value = true;
  synError.value = "";
  try {
    await apiSend(`/api/materials/synonyms/${s.synonym_id}`, "DELETE");
    await reloadSyns();
  } catch (err) {
    synError.value = err instanceof Error ? err.message : "Lỗi";
  } finally {
    synBusy.value = false;
  }
}

function closeSyn() {
  synVisible.value = false;
  resolveSyn?.();
  resolveSyn = null;
}

const rowActions: RowAction[] = [
  { label: "Đồng nghĩa", run: (r) => openSyn(r) },
  { label: "Xoá", severity: "danger", confirm: "Xoá vật tư?", run: (r) => apiSend(`/api/materials/${mid(r)}`, "DELETE") },
];
</script>

<template>
  <ResourcePage
    title="Từ điển vật tư"
    eyebrow="Materials"
    description="Danh mục vật tư và từ đồng nghĩa."
    :columns="columns"
    :load="load"
    :create-form="createForm"
    :row-actions="rowActions"
  />

  <Dialog v-model:visible="synVisible" :header="`Đồng nghĩa — ${synMatName}`" modal :style="{ width: '480px' }">
    <Message v-if="synError" severity="error" v-text="synError"></Message>
    <ul v-if="synList.length" class="syn-list">
      <li
        v-for="s in synList"
        :key="s.synonym_id"
        style="display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 4px 0"
      >
        <span v-text="s.synonym"></span>
        <Button label="Xoá" size="small" severity="danger" outlined :disabled="synBusy" @click="removeSyn(s)" />
      </li>
    </ul>
    <p v-else class="muted-text">Chưa có từ đồng nghĩa.</p>
    <form class="inline-form" style="display: flex; gap: 8px; margin-top: 12px" @submit.prevent="addSyn">
      <InputText v-model="newSyn" placeholder="Từ đồng nghĩa mới" style="flex: 1" />
      <Button type="submit" label="Thêm" :disabled="synBusy || !newSyn.trim()" />
    </form>
    <div class="form-actions" style="margin-top: 12px">
      <Button label="Đóng" severity="secondary" outlined @click="closeSyn" />
    </div>
  </Dialog>
</template>
