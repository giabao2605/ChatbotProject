<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { apiGet } from '@/api/client';
import type { EvidenceDocument, ApprovedScopeChoice } from '@/types';

const props = defineProps<{label:string; documents:EvidenceDocument[]; modelValue:string; disabled?:boolean;
  template?:'scope_comparison'|'scope_total'}>();
const emit = defineEmits<{'update:modelValue':[value:string]}>();
const doc = ref('');
const scopes = ref<ApprovedScopeChoice[]>([]);
const offset = ref(0);
const hasMore = ref(false);
const loading = ref(false);
const error = ref('');
let serial = 0;

async function load(nextOffset=0) {
  const request = ++serial;
  scopes.value = [];
  hasMore.value = false;
  emit('update:modelValue','');
  error.value = '';
  loading.value = Boolean(doc.value);
  if (!doc.value) return;
  try {
    const page = await apiGet<{items:ApprovedScopeChoice[];offset:number;has_more:boolean}>(
      `/api/rag-evidence/documents/${doc.value}/approved-scopes`,
      {offset:nextOffset,limit:50,...(props.template ? {template:props.template} : {})});
    if (request !== serial) return;
    scopes.value = page.items;
    offset.value = page.offset;
    hasMore.value = page.has_more;
  } catch {
    if (request === serial) error.value = 'Không tải được phạm vi đã duyệt. Hãy thử lại.';
  } finally {
    if (request === serial) loading.value = false;
  }
}
watch([doc,()=>props.template],()=>load());
onBeforeUnmount(()=>{serial++;});
</script>

<template>
  <div class="scope-picker">
    <label>Tài liệu {{ label }}
      <select v-model="doc" :aria-label="`Tài liệu ${label}`" :disabled="disabled">
        <option value="">Chọn tài liệu</option>
        <option v-for="item in documents" :key="item.DocID" :value="String(item.DocID)">{{ item.TenFile }}</option>
      </select>
    </label>
    <label>{{ label }}
      <select :value="modelValue" :aria-label="label" :disabled="disabled || loading || !scopes.length"
        @change="emit('update:modelValue',($event.target as HTMLSelectElement).value)">
        <option value="">Chọn phạm vi đã duyệt</option>
        <option v-for="item in scopes" :key="item.scope_id" :value="item.scope_id">
          {{ [item.definition,item.period,item.unit].filter(Boolean).join(' · ') }}
        </option>
      </select>
    </label>
    <p v-if="loading" role="status">Đang tải phạm vi…</p>
    <p v-else-if="doc && !scopes.length && !error">Chưa có phạm vi đã duyệt khả dụng.</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="doc">
      <button type="button" :disabled="disabled || loading || offset === 0" @click="load(Math.max(0,offset-50))">Trang trước</button>
      <button type="button" :disabled="disabled || loading || !hasMore" @click="load(offset+50)">Trang sau</button>
      <button v-if="error" type="button" :disabled="disabled || loading" @click="load(offset)">Thử lại</button>
    </div>
  </div>
</template>

<style scoped>
.scope-picker { display:grid; gap:.4rem; }
label { display:grid; gap:.2rem; }
select,button { background:var(--surface); color:var(--text); border:1px solid var(--border); padding:.4rem; border-radius:4px; }
button { margin-right:.3rem; cursor:pointer; }
p { margin:0; font-size:.85rem; }
</style>
