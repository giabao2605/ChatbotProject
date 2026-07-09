<script setup lang="ts">
import { onMounted, ref } from "vue";
import { t } from "@/i18n";
import type { ToolbarAction } from "@/types";

const props = defineProps<{
  title: string;
  eyebrow?: string;
  description?: string;
  load: () => Promise<unknown>;
  toolbar?: ToolbarAction[];
}>();

const loading = ref(false);
const error = ref("");
const busy = ref(false);

type Scalar = { key: string; value: string };
type Cell = { text: string; pct: number | null };
type TableSection = {
  kind: "table";
  key: string;
  columns: string[];
  rows: Cell[][];
};
type BarItem = { label: string; value: number; text: string; pct: number };
type BarSection = { kind: "bars"; key: string; items: BarItem[] };
type Section = TableSection | BarSection;

const scalars = ref<Scalar[]>([]);
const sections = ref<Section[]>([]);

function isScalar(v: unknown): boolean {
  return v === null || ["string", "number", "boolean"].includes(typeof v);
}

function toNumber(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "" && Number.isFinite(Number(v))) return Number(v);
  return null;
}

function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  const n = toNumber(v);
  if (n !== null && typeof v === "number") {
    return Number.isInteger(n) ? String(n) : n.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  }
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

// Bang tu mang cac object: cot so se co thanh bar ty le theo gia tri lon nhat cot do.
function buildTable(key: string, arr: Record<string, unknown>[]): TableSection {
  const columns: string[] = [];
  for (const item of arr) {
    for (const k of Object.keys(item ?? {})) if (!columns.includes(k)) columns.push(k);
  }
  const maxByCol: Record<string, number> = {};
  const numericCol: Record<string, boolean> = {};
  for (const col of columns) {
    let allNum = true;
    let hasNum = false;
    let max = 0;
    for (const item of arr) {
      const raw = item?.[col];
      if (raw === null || raw === undefined || raw === "") continue;
      const n = toNumber(raw);
      if (n === null) {
        allNum = false;
      } else {
        hasNum = true;
        if (Math.abs(n) > max) max = Math.abs(n);
      }
    }
    numericCol[col] = allNum && hasNum;
    maxByCol[col] = max || 1;
  }
  const rows: Cell[][] = arr.map((item) =>
    columns.map((col) => {
      const raw = item?.[col];
      const n = numericCol[col] ? toNumber(raw) : null;
      return {
        text: fmt(raw),
        pct: n === null ? null : Math.round((Math.abs(n) / maxByCol[col]) * 100),
      };
    }),
  );
  return { kind: "table", key, columns, rows };
}

function buildBars(key: string, obj: Record<string, unknown>): BarSection {
  const entries = Object.entries(obj).map(([label, v]) => ({ label, n: toNumber(v) ?? 0, raw: v }));
  const max = Math.max(1, ...entries.map((e) => Math.abs(e.n)));
  const items: BarItem[] = entries
    .sort((a, b) => Math.abs(b.n) - Math.abs(a.n))
    .map((e) => ({ label: e.label, value: e.n, text: fmt(e.raw), pct: Math.round((Math.abs(e.n) / max) * 100) }));
  return { kind: "bars", key, items };
}

function addSection(key: string, value: unknown) {
  if (Array.isArray(value)) {
    if (!value.length) return;
    if (value.every((v) => v && typeof v === "object" && !Array.isArray(v))) {
      sections.value.push(buildTable(key, value as Record<string, unknown>[]));
    } else {
      // Mang gia tri don -> bang 1 cot.
      sections.value.push(buildTable(key, value.map((v) => ({ value: v }))));
    }
    return;
  }
  if (value && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const vals = Object.values(obj);
    if (vals.length && vals.every((v) => toNumber(v) !== null)) {
      sections.value.push(buildBars(key, obj));
    } else {
      // Object long -> bang key/value.
      sections.value.push(buildTable(key, Object.entries(obj).map(([k, v]) => ({ key: k, value: fmt(v) }))));
    }
  }
}

async function refresh() {
  loading.value = true;
  error.value = "";
  try {
    const result = (await props.load()) ?? {};
    scalars.value = [];
    sections.value = [];
    if (Array.isArray(result)) {
      addSection(props.title, result);
    } else if (result && typeof result === "object") {
      for (const [key, value] of Object.entries(result as Record<string, unknown>)) {
        if (isScalar(value)) scalars.value.push({ key, value: fmt(value) });
        else addSection(key, value);
      }
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("common.error");
  } finally {
    loading.value = false;
  }
}

async function runToolbar(action: ToolbarAction) {
  if (action.confirm && !window.confirm(action.confirm)) return;
  busy.value = true;
  try {
    await action.run();
    await refresh();
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("common.error");
  } finally {
    busy.value = false;
  }
}

function eyebrowText(): string {
  return props.eyebrow || "Operations";
}

onMounted(refresh);
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
          :disabled="busy"
          @click="runToolbar(action)"
        />
        <Button :label="t('common.refresh')" severity="secondary" outlined :disabled="loading" @click="refresh" />
      </div>
    </header>

    <Message v-if="error" severity="error" v-text="error"></Message>
    <div v-if="loading" class="loading-block"><ProgressSpinner /></div>

    <template v-else>
      <div v-if="scalars.length" class="stat-grid">
        <Card v-for="item in scalars" :key="item.key">
          <template #title><span v-text="item.key"></span></template>
          <template #content><strong class="stat-number" v-text="item.value"></strong></template>
        </Card>
      </div>

      <Card v-for="section in sections" :key="section.key">
        <template #title><span v-text="section.key"></span></template>
        <template #content>
          <!-- Bieu do cot ngang cho map so lieu -->
          <div v-if="section.kind === 'bars'" class="bar-chart">
            <div v-for="item in section.items" :key="item.label" class="bar-row">
              <span class="bar-label" v-text="item.label"></span>
              <span class="bar-track"><span class="bar-fill" :style="{ width: item.pct + '%' }"></span></span>
              <span class="bar-value" v-text="item.text"></span>
            </div>
          </div>
          <!-- Bang co bar noi tuyen cho cot so -->
          <div v-else class="stat-table-wrap">
            <table class="stat-table">
              <thead>
                <tr>
                  <th v-for="col in section.columns" :key="col" v-text="col"></th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, ri) in section.rows" :key="ri">
                  <td v-for="(cell, ci) in row" :key="ci">
                    <template v-if="cell.pct !== null">
                      <div class="cell-metric">
                        <span class="cell-bar" :style="{ width: cell.pct + '%' }"></span>
                        <span class="cell-num" v-text="cell.text"></span>
                      </div>
                    </template>
                    <span v-else v-text="cell.text"></span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
      </Card>
    </template>
  </section>
</template>

<style scoped>
.bar-chart {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.bar-row {
  display: grid;
  grid-template-columns: minmax(80px, 160px) 1fr auto;
  align-items: center;
  gap: 0.75rem;
}
.bar-label {
  font-size: 0.85rem;
  color: var(--p-text-muted-color, #6b7280);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.bar-track {
  position: relative;
  height: 12px;
  border-radius: 6px;
  background: var(--p-surface-200, #e5e7eb);
  overflow: hidden;
}
.bar-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 6px;
  background: var(--p-primary-color, #10b981);
  min-width: 2px;
}
.bar-value {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  font-size: 0.85rem;
}
.stat-table-wrap {
  overflow-x: auto;
}
.stat-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}
.stat-table th,
.stat-table td {
  text-align: left;
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid var(--p-surface-200, #e5e7eb);
  white-space: nowrap;
}
.stat-table th {
  color: var(--p-text-muted-color, #6b7280);
  font-weight: 600;
}
.cell-metric {
  position: relative;
  display: flex;
  align-items: center;
  min-width: 90px;
}
.cell-bar {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  border-radius: 4px;
  background: color-mix(in srgb, var(--p-primary-color, #10b981) 22%, transparent);
  min-width: 2px;
}
.cell-num {
  position: relative;
  font-variant-numeric: tabular-nums;
  padding: 0 0.35rem;
}
</style>
