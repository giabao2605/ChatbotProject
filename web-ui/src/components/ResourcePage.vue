<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import { t } from "@/i18n";
import type {
  ApiRow,
  CreateForm,
  ResourceColumn,
  ResourceFilter,
  RowAction,
  ToolbarAction,
} from "@/types";

const props = defineProps<{
  title: string;
  eyebrow?: string;
  description?: string;
  load: (filters: Record<string, unknown>) => Promise<ApiRow[]>;
  columns?: ResourceColumn[];
  rowActions?: RowAction[];
  toolbar?: ToolbarAction[];
  createForm?: CreateForm;
  filters?: ResourceFilter[];
}>();

const rows = ref<ApiRow[]>([]);
const columns = ref<ResourceColumn[]>([]);
const loading = ref(false);
const error = ref("");
const notice = ref("");
const busy = ref(false);

const filterValues = reactive<Record<string, unknown>>({});
for (const filter of props.filters ?? []) {
  filterValues[filter.key] = filter.value ?? (filter.type === "checkbox" ? false : "");
}

const showForm = ref(false);
const formValues = reactive<Record<string, unknown>>({});

function resetForm() {
  for (const field of props.createForm?.fields ?? []) {
    formValues[field.key] = field.type === "checkbox" ? false : field.type === "number" ? null : "";
  }
}

function humanize(key: string): string {
  return key.replace(/[_.]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function normalizeRows(raw: ApiRow[]): { display: ApiRow[]; cols: ResourceColumn[] } {
  if (!raw.length) return { display: [], cols: props.columns ?? [] };
  const first = raw[0] as unknown;
  if (Array.isArray(first)) {
    const width = (first as unknown[]).length;
    const cols: ResourceColumn[] =
      props.columns ??
      Array.from({ length: width }, (_, i) => ({ field: `c${i}`, header: `#${i + 1}` }));
    const display = raw.map((r) => {
      const arr = r as unknown as unknown[];
      const obj: ApiRow = { __raw: r };
      arr.forEach((value, i) => (obj[`c${i}`] = value));
      return obj;
    });
    return { display, cols };
  }
  const cols: ResourceColumn[] =
    props.columns ?? Object.keys(first as ApiRow).map((field) => ({ field, header: humanize(field) }));
  const display = raw.map((r) => ({ ...(r as ApiRow), __raw: r }));
  return { display, cols };
}

async function refresh() {
  loading.value = true;
  error.value = "";
  try {
    const raw = await props.load({ ...filterValues });
    const { display, cols } = normalizeRows(raw ?? []);
    rows.value = display;
    columns.value = cols;
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("common.error");
  } finally {
    loading.value = false;
  }
}

async function runAction(action: RowAction | ToolbarAction, row?: ApiRow) {
  if (action.confirm && !window.confirm(action.confirm)) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    if (row) {
      await (action as RowAction).run(row);
    } else {
      await (action as ToolbarAction).run();
    }
    notice.value = t("common.success");
    await refresh();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("common.error");
  } finally {
    busy.value = false;
  }
}

function openForm() {
  resetForm();
  showForm.value = true;
}

async function submitForm() {
  if (!props.createForm) return;
  busy.value = true;
  error.value = "";
  try {
    await props.createForm.submit({ ...formValues });
    showForm.value = false;
    notice.value = t("common.success");
    await refresh();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("common.error");
  } finally {
    busy.value = false;
  }
}

function cellText(row: ApiRow, col: ResourceColumn): string {
  const value = row[col.field];
  if (value === null || value === undefined) return "—";
  if (col.kind === "bool") return value ? t("common.yes") : t("common.no");
  if (col.kind === "score" && typeof value === "number") return value.toFixed(3);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function visibleActions(row: ApiRow): RowAction[] {
  return (props.rowActions ?? []).filter((a) => (a.visible ? a.visible(row) : true));
}

function eyebrowText(): string {
  return props.eyebrow || "Operations";
}

watch(
  () => ({ ...filterValues }),
  () => refresh(),
  { deep: true },
);

onMounted(refresh);
defineExpose({ refresh });
</script>

<template>
  <section class="content-page">
    <header class="page-header">
      <div>
        <div class="eyebrow" v-text="eyebrowText()"></div>
        <h1 v-text="title"></h1>
        <p v-if="description" class="page-subtitle" v-text="description"></p>
      </div>
      <div class="page-actions">
        <Button
          v-for="action in toolbar || []"
          :key="action.label"
          :label="action.label"
          :severity="action.severity || 'secondary'"
          :outlined="action.outlined"
          :disabled="busy"
          @click="runAction(action)"
        />
        <Button
          v-if="createForm"
          :label="createForm.triggerLabel || t('common.create')"
          @click="openForm"
        />
        <Button
          :label="t('common.refresh')"
          severity="secondary"
          outlined
          :disabled="loading"
          @click="refresh"
        />
      </div>
    </header>

    <div v-if="filters && filters.length" class="filter-bar">
      <label v-for="filter in filters" :key="filter.key" class="filter-field">
        <span v-text="filter.label"></span>
        <select
          v-if="filter.type === 'select'"
          v-model="filterValues[filter.key]"
          class="native-select"
        >
          <option value="">—</option>
          <option
            v-for="opt in filter.options || []"
            :key="String(opt.value)"
            :value="opt.value"
            v-text="opt.label"
          ></option>
        </select>
        <input
          v-else-if="filter.type === 'checkbox'"
          v-model="filterValues[filter.key]"
          type="checkbox"
        />
        <InputText v-else v-model="filterValues[filter.key] as string" />
      </label>
    </div>

    <Message v-if="error" severity="error" v-text="error"></Message>
    <Message v-if="notice && !error" severity="success" v-text="notice"></Message>

    <div v-if="loading" class="loading-block">
      <ProgressSpinner />
    </div>

    <Card v-else>
      <template #content>
        <p v-if="!rows.length" class="muted-text" v-text="t('common.empty')"></p>
        <DataTable v-else :value="rows" paginator :rows="15" removable-sort>
          <Column
            v-for="col in columns"
            :key="col.field"
            :field="col.field"
            :header="col.header"
            :style="col.width ? { width: col.width } : undefined"
            sortable
          >
            <template #body="{ data }">
              <Tag v-if="col.kind === 'tag' && data[col.field] != null" :value="cellText(data, col)" />
              <code v-else-if="col.kind === 'code'" v-text="cellText(data, col)"></code>
              <span v-else v-text="cellText(data, col)"></span>
            </template>
          </Column>
          <Column v-if="rowActions && rowActions.length" :header="t('common.actions')" style="width: 220px">
            <template #body="{ data }">
              <div class="row-actions">
                <Button
                  v-for="action in visibleActions(data)"
                  :key="action.label"
                  :label="action.label"
                  size="small"
                  :severity="action.severity || 'info'"
                  :outlined="action.outlined === true"
                  :disabled="busy"
                  @click="runAction(action, data)"
                />
              </div>
            </template>
          </Column>
        </DataTable>
      </template>
    </Card>

    <Dialog
      v-if="createForm"
      v-model:visible="showForm"
      :header="createForm.title || t('common.create')"
      modal
      :style="{ width: '480px' }"
    >
      <form class="stack-form" @submit.prevent="submitForm">
        <label v-for="field in createForm.fields" :key="field.key" class="stack-field">
          <span><span v-text="field.label"></span><em v-if="field.required"> *</em></span>
          <Textarea
            v-if="field.type === 'textarea'"
            v-model="formValues[field.key] as string"
            rows="3"
            auto-resize
          />
          <select
            v-else-if="field.type === 'select'"
            v-model="formValues[field.key]"
            class="native-select"
          >
            <option
              v-for="opt in field.options || []"
              :key="String(opt.value)"
              :value="opt.value"
              v-text="opt.label"
            ></option>
          </select>
          <input
            v-else-if="field.type === 'checkbox'"
            v-model="formValues[field.key]"
            type="checkbox"
          />
          <InputText
            v-else-if="field.type === 'number'"
            v-model="formValues[field.key] as string"
            type="number"
          />
          <InputText v-else v-model="formValues[field.key] as string" :placeholder="field.placeholder" />
          <small v-if="field.help" class="muted-text" v-text="field.help"></small>
        </label>
        <div class="form-actions">
          <Button type="button" :label="t('common.cancel')" severity="secondary" outlined @click="showForm = false" />
          <Button type="submit" :label="t('common.save')" :loading="busy" />
        </div>
      </form>
    </Dialog>
  </section>
</template>
