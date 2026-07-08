<script setup lang="ts">
import { onMounted, ref } from "vue";
import { t } from "@/i18n";
import type { ToolbarAction } from "@/types";

const props = defineProps<{
  title: string;
  eyebrow?: string;
  description?: string;
  load: () => Promise<Record<string, unknown>>;
  toolbar?: ToolbarAction[];
}>();

const loading = ref(false);
const error = ref("");
const busy = ref(false);

const scalars = ref<Array<{ key: string; value: string }>>([]);
const blocks = ref<Array<{ key: string; value: string }>>([]);

function isScalar(v: unknown): boolean {
  return v === null || ["string", "number", "boolean"].includes(typeof v);
}

async function refresh() {
  loading.value = true;
  error.value = "";
  try {
    const result = (await props.load()) ?? {};
    scalars.value = [];
    blocks.value = [];
    for (const [key, value] of Object.entries(result)) {
      if (isScalar(value)) scalars.value.push({ key, value: String(value ?? "—") });
      else blocks.value.push({ key, value: JSON.stringify(value, null, 2) });
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
      <Card v-for="block in blocks" :key="block.key">
        <template #title><span v-text="block.key"></span></template>
        <template #content><pre class="json-block" v-text="block.value"></pre></template>
      </Card>
    </template>
  </section>
</template>
