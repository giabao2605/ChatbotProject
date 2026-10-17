<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { apiGet } from '@/api/client';
import type { Citation } from '@/types';

type Fact = { fact_id:string; label:string; raw_value:string; value:string|null; unit:string; locator:NonNullable<Citation['locator']> };
const props = defineProps<{ citation:Citation }>();
const emit = defineEmits<{ unavailable:[] }>();
const opened = ref(false);
const loading = ref(false);
const error = ref('');
const rows = ref<Fact[]>([]);
const offset = ref(0);
const hasMore = ref(false);
let serial = 0;

async function load(pageOffset = 0) {
  const request = ++serial;
  opened.value = true;
  loading.value = true;
  error.value = '';
  rows.value = [];
  hasMore.value = false;
  try {
    const page = await apiGet<{items:Fact[];snapshot_id:string;scope_hash:string;offset:number;has_more:boolean;ok?:boolean}>(
      `/api/rag-evidence/approved-scopes/${encodeURIComponent(props.citation.scope_id!)}/facts`, {offset:pageOffset,limit:50});
    if (request !== serial) return;
    if (page.ok === false || page.snapshot_id !== props.citation.snapshot_id || page.scope_hash !== props.citation.scope_hash) {
      error.value = 'Nguồn đã thay đổi hoặc không còn khả dụng. Hãy hỏi lại để lấy nguồn hiện hành.';
      emit('unavailable');
      return;
    }
    rows.value = page.items;
    offset.value = page.offset;
    hasMore.value = page.has_more;
  } catch (err) {
    if (request === serial) {
      error.value = 'Không tải được số liệu nguồn. Nguồn có thể đã thay đổi hoặc bạn không còn quyền xem.';
      if (err instanceof Error && err.message === 'Evidence unavailable') emit('unavailable');
    }
  } finally {
    if (request === serial) loading.value = false;
  }
}

watch(() => [props.citation.source_id, props.citation.scope_id, props.citation.snapshot_id, props.citation.scope_hash], () => {
  serial++;
  opened.value = false;
  loading.value = false;
  rows.value = [];
  error.value = '';
  offset.value = 0;
  hasMore.value = false;
});
onBeforeUnmount(() => serial++);
</script>

<template>
  <section v-if="citation.scope_id && citation.snapshot_id && citation.scope_hash" class="source-facts" :aria-busy="loading">
    <button type="button" :disabled="loading" @click="load()">{{ opened ? 'Tải lại số liệu nguồn' : 'Xem số liệu nguồn' }}</button>
    <p v-if="loading" role="status">Đang tải số liệu nguồn</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="opened && !loading && !error">
      <table>
        <caption>Số liệu đã duyệt dùng làm căn cứ</caption>
        <thead><tr><th scope="col">Vị trí</th><th scope="col">Trường</th><th scope="col">Giá trị gốc</th><th scope="col">Giá trị tính toán</th><th scope="col">Đơn vị</th></tr></thead>
        <tbody><tr v-for="row in rows" :key="row.fact_id">
          <td><span v-if="row.locator.page">Trang {{ row.locator.page }} </span>{{ row.locator.sheet }} {{ row.locator.cell }} {{ row.locator.row_key }}</td>
          <td>{{ row.label }}</td><td>{{ row.raw_value }}</td><td>{{ row.value }}</td><td>{{ row.unit }}</td>
        </tr></tbody>
      </table>
      <p v-if="!rows.length">Không có dòng dữ liệu trong trang này.</p>
      <nav aria-label="Phân trang số liệu nguồn">
        <button type="button" :disabled="offset === 0" @click="load(Math.max(0, offset - 50))">Trang trước</button>
        <span>Trang {{ Math.floor(offset / 50) + 1 }}</span>
        <button type="button" :disabled="!hasMore" @click="load(offset + 50)">Trang sau</button>
      </nav>
    </template>
  </section>
</template>

<style scoped>
.source-facts { min-width:0; padding:0.75rem; }
button { min-height:44px; padding:0.5rem 0.75rem; cursor:pointer; color:var(--text); background:var(--surface); border:1px solid var(--border); border-radius:6px; }
button:disabled { cursor:default; }
table { width:100%; border-collapse:collapse; table-layout:fixed; margin-top:0.75rem; }
th, td { text-align:left; vertical-align:top; padding:0.5rem; overflow-wrap:anywhere; border-bottom:1px solid var(--border); }
caption { text-align:left; font-weight:600; padding:0.5rem 0; }
th:nth-child(1) { width:20%; }
th:nth-child(2) { width:14%; }
th:nth-child(3), th:nth-child(4) { width:28%; }
th:nth-child(5) { width:10%; }
td:nth-child(3), td:nth-child(4) { font-variant-numeric:tabular-nums; }
nav { display:flex; flex-wrap:wrap; align-items:center; gap:0.5rem; margin-top:0.5rem; }
</style>
