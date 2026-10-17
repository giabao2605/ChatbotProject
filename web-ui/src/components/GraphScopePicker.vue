<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue';
import { apiGet } from '@/api/client';

type Choice={product_scope:string;site:string};
const props=defineProps<{docId:string;site:string;label:string;modelValue:string;disabled?:boolean}>();
const emit=defineEmits<{'update:modelValue':[value:string]}>();
const scopes=ref<Choice[]>([]);
const offset=ref(0);
const hasMore=ref(false);
const loading=ref(false);
const error=ref('');
let serial=0;

async function load(nextOffset=0) {
  const request=++serial;
  scopes.value=[];hasMore.value=false;offset.value=nextOffset;
  emit('update:modelValue','');
  error.value='';loading.value=Boolean(props.docId && props.site);
  if (!loading.value) return;
  try {
    const page=await apiGet<{items:Choice[];offset:number;has_more:boolean}>(
      `/api/rag-evidence/documents/${props.docId}/graph-scopes`,{site:props.site,offset:nextOffset,limit:50});
    if (request!==serial) return;
    scopes.value=page.items.filter(item=>item.site===props.site);
    offset.value=page.offset;hasMore.value=page.has_more;
  } catch {
    if (request===serial) error.value='Không tải được phạm vi sản phẩm đã duyệt. Hãy thử lại.';
  } finally {
    if (request===serial) loading.value=false;
  }
}
function choose(event:Event) {
  if (!props.disabled) emit('update:modelValue',(event.target as HTMLSelectElement).value);
}
watch(()=>[props.docId,props.site],()=>load(),{immediate:true});
onBeforeUnmount(()=>{serial++;});
</script>

<template>
  <div class="graph-scope-picker">
    <label>Phạm vi sản phẩm
      <select :value="modelValue" :aria-label="label" :disabled="disabled || loading || !scopes.length" @change="choose">
        <option value="">Chọn phạm vi đã duyệt</option>
        <option v-for="item in scopes" :key="item.product_scope" :value="item.product_scope">{{ item.product_scope }}</option>
      </select>
    </label>
    <p v-if="loading" role="status">Đang tải phạm vi…</p>
    <p v-else-if="!docId || !site">Chọn bản vẽ và site để xem phạm vi đã duyệt.</p>
    <p v-else-if="!scopes.length && !error">Chưa có phạm vi đã duyệt khả dụng.</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="docId && site">
      <button type="button" :disabled="disabled || loading || offset===0" @click="load(Math.max(0,offset-50))">Trang trước</button>
      <button type="button" :disabled="disabled || loading || !hasMore" @click="load(offset+50)">Trang sau</button>
      <button v-if="error" type="button" :disabled="disabled || loading" @click="load(offset)">Thử lại</button>
    </div>
  </div>
</template>

<style scoped>
.graph-scope-picker,label { display:grid; gap:.2rem; }
select,button { background:var(--surface); color:var(--text); border:1px solid var(--border); padding:.4rem; border-radius:4px; min-width:0; }
button { margin-right:.3rem; cursor:pointer; }
p { margin:0; font-size:.85rem; color:var(--muted); }
</style>
