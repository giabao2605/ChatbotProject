<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { apiGet } from '@/api/client';
import { useAuthStore } from '@/stores/auth';
import ApprovedScopePicker from '@/components/ApprovedScopePicker.vue';
import GraphScopePicker from '@/components/GraphScopePicker.vue';
import type { EvidenceDocument, QueryAction, QueryActionState } from '@/types';

type Action = 'contains'|'where_used'|'bom_rollup'|'scope_comparison'|'scope_total';
type Draft = {id:string;action:Action;question:string;doc:string;part:string;product:string;site:string;
  quantity:string;quantityMode:'literal'|'source';quantityScope:string;scopeA:string;scopeB:string;policy:string;dependencies:string[]};
const labels:Record<Action,string> = {contains:'Cụm chứa gì?',where_used:'Chi tiết dùng ở đâu?',
  bom_rollup:'Tổng hợp BOM',scope_comparison:'So sánh hai phạm vi',scope_total:'Tổng một phạm vi'};
const props=defineProps<{disabled:boolean}>();
const emit=defineEmits<{change:[state:QueryActionState]}>();
const auth=useAuthStore();
const available=ref(false);
const comparisonPilot=ref(false);
const graphPilot=ref(false);
const combinedPilot=ref(false);
const totalPilot=ref(false);
const mathTemplatesPilot=ref(false);
const queryPilot=ref(false);
const standalone=computed(()=>comparisonPilot.value || graphPilot.value || combinedPilot.value || totalPilot.value || mathTemplatesPilot.value);
const textAvailable=ref(false);
const totalAvailable=ref(false);
const totalQueryAvailable=ref(false);
const evaluationLabels=computed(()=>Object.fromEntries(Object.entries(labels).filter(([action])=>action!=='scope_total' || (totalAvailable.value && (rows.value.length===1 || totalQueryAvailable.value)))));
const mode=ref<'structured'|'text'>('structured');
const enabled=ref(false);
const loading=ref(false);
const error=ref('');
const documents=ref<EvidenceDocument[]>([]);
const rows=ref<Draft[]>([]);
let capabilitySerial=0;
let documentSerial=0;
let nextId=0;

function draft(action:Action='contains',id=`b${++nextId}`):Draft {
  return {id,action,question:labels[action],doc:'',part:'',product:'',
    site:auth.user?.allowed_sites.length===1 ? auth.user.allowed_sites[0] : '',
    quantity:'1',quantityMode:'literal',quantityScope:'',scopeA:'',scopeB:'',policy:'',dependencies:[]};
}
function update(id:string,values:Partial<Draft>) {
  if (!props.disabled) rows.value=rows.value.map(row=>row.id===id?{...row,...values,
    ...(values.doc!==undefined || values.site!==undefined || values.product!==undefined || values.quantityMode!==undefined ? {quantityScope:''} : {}),
    ...(values.doc!==undefined || values.site!==undefined ? {product:''} : {})}:row);
}
function changeAction(row:Draft,action:Action) {
  update(row.id,draft(action,row.id));
}
function remove(id:string) {
  if (props.disabled) return;
  rows.value=rows.value.filter(row=>row.id!==id).map(row=>({...row,dependencies:row.dependencies.filter(v=>v!==id)}));
}
function depend(row:Draft,id:string,checked:boolean) {
  update(row.id,{dependencies:checked?[...row.dependencies,id]:row.dependencies.filter(v=>v!==id)});
}
const state=computed<QueryActionState>(()=>{
  if (!available.value || !enabled.value) return {active:false,valid:true};
  if (mode.value==='text' && textAvailable.value) return {active:true,valid:true,text_query:true};
  if (rows.value.length===1 && rows.value[0]?.action==='scope_total') {
    const row=rows.value[0];
    return {active:true,valid:totalAvailable.value && !loading.value && !error.value && rows.value.length===1
      && row.action==='scope_total' && Boolean(row.scopeA && row.question.trim()),scope_total:{scope_id:row.scopeA}};
  }
  const branches:QueryAction[]=rows.value.map(row=>{
    const graph={root_key:row.action==='where_used'?`part:${row.part.trim()}`:`document:${row.doc}`,
      product_scope:row.product.trim(),site:row.site,direction:row.action==='where_used'?'where_used' as const:'contains' as const};
    return {branch_id:row.id,question:row.question.trim(),depends_on:[...row.dependencies],
      ...(row.action==='scope_total'?{scope_total:{scope_id:row.scopeA}}:row.action==='scope_comparison'?{scope_comparison:{scope_a:row.scopeA,scope_b:row.scopeB}}:
        row.action==='bom_rollup'?{bom_rollup:{...graph,...(row.quantityMode==='source'?{quantity_scope:row.quantityScope}:{order_quantity:row.quantity}),rounding_scope:row.policy}}:{graph_query:graph})};
  });
  const valid=!loading.value && !error.value && rows.value.length>=1 && rows.value.length<=3 && rows.value.every(row=>
    Boolean(row.question.trim()) && (row.action==='scope_total'?Boolean(totalQueryAvailable.value && row.scopeA):row.action==='scope_comparison'?Boolean(row.scopeA && row.scopeB):
      Boolean(row.product.trim() && row.site && (row.action==='where_used'?row.part.trim():row.doc)
        && (row.action!=='bom_rollup' || ((row.quantityMode==='source'?Boolean(row.quantityScope):/^[0-9]+(?:\.[0-9]+)?$/.test(row.quantity)) && row.policy)))));
  if (comparisonPilot.value || ((combinedPilot.value || mathTemplatesPilot.value) && rows.value[0]?.action==='scope_comparison')) return {active:true,valid:valid && rows.value.length===1 && rows.value[0]?.action==='scope_comparison'
    && rows.value[0].scopeA!==rows.value[0].scopeB,scope_comparison:branches[0]?.scope_comparison};
  if (graphPilot.value || combinedPilot.value) return {active:true,valid:valid && rows.value.length===1
    && ['contains','where_used'].includes(rows.value[0]?.action),graph_query:branches[0]?.graph_query};
  return {active:true,valid,query_plan:{branches}};
});
watch(state,value=>emit('change',value),{immediate:true});
watch(()=>JSON.stringify(auth.user),async()=>{
  const request=++capabilitySerial;
  documentSerial++;
  available.value=false;enabled.value=false;rows.value=[];documents.value=[];error.value='';loading.value=false;
  textAvailable.value=false;totalAvailable.value=false;totalQueryAvailable.value=false;comparisonPilot.value=false;graphPilot.value=false;combinedPilot.value=false;totalPilot.value=false;mathTemplatesPilot.value=false;queryPilot.value=false;mode.value='structured';
  if (!auth.user) return;
  try {
    const capabilities=await apiGet<{schema:string;contract?:string;accepted:boolean;offline_only:boolean;max_branches:number;actions:string[];text_query?:boolean;scope_total_query?:boolean}>(
      '/api/rag-evidence/evaluation-capabilities');
    if (request!==capabilitySerial) return;
    queryPilot.value=capabilities.schema==='advanced-rag-v2-controlled-demo'
      && capabilities.contract==='query-reviewed-actions-controlled-demo-v1' && capabilities.accepted===false
      && capabilities.offline_only===false && capabilities.max_branches===3 && capabilities.text_query===true
      && capabilities.scope_total_query===true && capabilities.actions?.length===5
      && ['contains','where_used','bom_rollup','scope_comparison','scope_total'].every(action=>capabilities.actions.includes(action));
    mathTemplatesPilot.value=capabilities.schema==='advanced-rag-v2-controlled-demo'
      && capabilities.contract==='math-reviewed-templates-controlled-demo-v1' && capabilities.accepted===false
      && capabilities.offline_only===false && capabilities.max_branches===1 && capabilities.text_query===false
      && capabilities.actions?.length===2 && ['scope_comparison','scope_total'].every(action=>capabilities.actions.includes(action));
    totalPilot.value=capabilities.schema==='advanced-rag-v2-controlled-demo'
      && capabilities.contract==='grounded-math-scope-total-controlled-demo-v1' && capabilities.accepted===false
      && capabilities.offline_only===false && capabilities.max_branches===1 && capabilities.text_query===false
      && capabilities.actions?.length===1 && capabilities.actions[0]==='scope_total';
    comparisonPilot.value=capabilities.schema==='advanced-rag-v2-controlled-demo'
      && capabilities.contract==='grounded-math-v2-comparison-pilot-v1' && capabilities.offline_only===false
      && capabilities.max_branches===1 && capabilities.actions?.length===1 && capabilities.actions[0]==='scope_comparison'
      && capabilities.text_query===false;
    graphPilot.value=capabilities.schema==='advanced-rag-v2-controlled-demo'
      && capabilities.contract==='graph-v2-reviewed-query-controlled-demo-v1' && capabilities.accepted===false
      && capabilities.offline_only===false && capabilities.max_branches===1 && capabilities.text_query===false
      && capabilities.actions?.length===2 && capabilities.actions.includes('contains') && capabilities.actions.includes('where_used');
    combinedPilot.value=capabilities.schema==='advanced-rag-v2-controlled-demo'
      && capabilities.contract==='math-graph-v2-controlled-demo-v1' && capabilities.accepted===false
      && capabilities.offline_only===false && capabilities.max_branches===1 && capabilities.text_query===false
      && capabilities.actions?.length===3 && ['contains','where_used','scope_comparison'].every(action=>capabilities.actions.includes(action));
    available.value=standalone.value || queryPilot.value || (auth.user.roles.some(role=>['reviewer','knowledge_approver','admin'].includes(role.toLowerCase()))
      && capabilities.schema==='advanced-rag-v2-evaluation' && capabilities.accepted===false
      && capabilities.offline_only===true && capabilities.max_branches===3
      && ['contains','where_used','bom_rollup','scope_comparison'].every(action=>capabilities.actions?.includes(action)));
    totalAvailable.value=totalPilot.value || mathTemplatesPilot.value || (!standalone.value && available.value && capabilities.actions?.includes('scope_total'));
    totalQueryAvailable.value=!standalone.value && totalAvailable.value && capabilities.scope_total_query===true;
    textAvailable.value=!standalone.value && available.value && capabilities.text_query===true;
  } catch {
    if (request===capabilitySerial) available.value=false;
  }
},{immediate:true});
watch(enabled,async value=>{
  const request=++documentSerial;
  rows.value=value?[draft(totalPilot.value?'scope_total':comparisonPilot.value || mathTemplatesPilot.value?'scope_comparison':'contains')]:[];
  error.value='';loading.value=value;
  if (!value) return;
  try {
    const data=await apiGet<{documents:EvidenceDocument[]}>('/api/documents',{bucket:'effective'});
    if (request===documentSerial) documents.value=data.documents;
  } catch {
    if (request===documentSerial) error.value='Không tải được danh sách tài liệu. Hãy tắt và mở lại tác vụ.';
  } finally {
    if (request===documentSerial) loading.value=false;
  }
});
onBeforeUnmount(()=>{capabilitySerial++;documentSerial++;});
</script>

<template>
  <section v-if="available" class="advanced-actions">
    <label><input v-model="enabled" type="checkbox" :disabled="disabled" /> {{ queryPilot ? 'Chọn tác vụ có căn cứ (bật thử)' : mathTemplatesPilot ? 'Tính tổng hoặc so sánh có căn cứ (bật thử)' : totalPilot ? 'Tính tổng có căn cứ (bật thử)' : combinedPilot ? 'Tra bản vẽ hoặc so sánh số liệu có căn cứ (bật thử)' : comparisonPilot ? 'So sánh số liệu có căn cứ (bật thử)' : graphPilot ? 'Tra quan hệ bản vẽ có căn cứ (bật thử)' : 'Chọn tác vụ có căn cứ (đánh giá)' }}</label>
    <template v-if="enabled">
      <label v-if="textAvailable">{{ queryPilot ? 'Chế độ trả lời' : 'Chế độ đánh giá' }}
        <select v-model="mode" aria-label="Chế độ đánh giá" :disabled="disabled">
          <option value="structured">Chọn tác vụ và phạm vi</option>
          <option value="text">Tự tách câu hỏi văn bản</option>
        </select>
      </label>
      <p v-if="mode==='text'">Nhập tối đa ba ý độc lập trong ô chat. Hệ thống tìm nguồn theo từng ý và sinh một câu trả lời chung. Phép tính dùng tác vụ Math với phạm vi đã duyệt.</p>
      <template v-else>
      <p v-if="queryPilot">Chọn tối đa ba ý và nguồn đã duyệt. Máy chủ kiểm tra quyền, phiên bản và độ đầy đủ của nguồn cho từng ý.</p>
      <p v-else-if="mathTemplatesPilot">Chọn một tác vụ: tính tổng một phạm vi hoặc so sánh hai phạm vi đã duyệt. Máy chủ kiểm tra quyền và phiên bản nguồn của từng tác vụ.</p>
      <p v-else-if="totalPilot">Chọn một phạm vi số liệu đã duyệt để cộng đủ các dòng. Máy chủ kiểm tra quyền và phiên bản nguồn khi gửi.</p>
      <p v-else-if="comparisonPilot">Chọn hai phạm vi số liệu đã duyệt để so sánh. Phạm vi được phép dùng do máy chủ kiểm tra khi gửi.</p>
      <p v-else-if="graphPilot">Chọn bản vẽ và phạm vi quan hệ đã duyệt. Kết quả cho biết phần đã xác nhận và độ đầy đủ của nguồn.</p>
      <p v-else-if="combinedPilot">Chọn một tác vụ và nguồn đã duyệt: tra quan hệ bản vẽ hoặc so sánh hai phạm vi số liệu.</p>
      <p v-else>Chọn tối đa ba ý và nguồn đã duyệt. Các tác vụ này xác định phạm vi trả lời; kết quả chưa phải nghiệm thu phát hành.</p>
      <p v-if="loading" role="status">Đang tải tài liệu…</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <fieldset v-for="(row,index) in rows" :key="row.id" :disabled="disabled || loading">
        <legend>Ý {{ index+1 }}</legend>
        <label>Tác vụ
          <select :value="row.action" :aria-label="`Tác vụ ý ${index+1}`"
            @change="changeAction(row,($event.target as HTMLSelectElement).value as Action)">
            <option v-for="(label,action) in (mathTemplatesPilot ? {scope_comparison:labels.scope_comparison,scope_total:labels.scope_total} : totalPilot ? {scope_total:labels.scope_total} : comparisonPilot ? {scope_comparison:labels.scope_comparison} : graphPilot ? {contains:labels.contains,where_used:labels.where_used} : combinedPilot ? {contains:labels.contains,where_used:labels.where_used,scope_comparison:labels.scope_comparison} : evaluationLabels)" :key="action" :value="action">{{ label }}</option>
          </select>
        </label>
        <label>Nội dung ý {{ index+1 }}
          <input :value="row.question" :aria-label="`Nội dung ý ${index+1}`" maxlength="20000"
            @input="update(row.id,{question:($event.target as HTMLInputElement).value})" />
        </label>
        <template v-if="row.action==='scope_total'">
          <ApprovedScopePicker template="scope_total" :documents="documents" :label="`Phạm vi tính tổng ý ${index+1}`" :model-value="row.scopeA" :disabled="disabled"
            @update:model-value="update(row.id,{scopeA:$event})" />
          <p>Cộng đủ dòng trong phạm vi đã duyệt. <template v-if="!standalone">Có thể kết hợp tối đa ba tác vụ khi máy chủ hỗ trợ.</template></p>
        </template>
        <template v-else-if="row.action==='scope_comparison'">
          <ApprovedScopePicker template="scope_comparison" :documents="documents" :label="`Phạm vi A ý ${index+1}`" :model-value="row.scopeA" :disabled="disabled"
            @update:model-value="update(row.id,{scopeA:$event})" />
          <ApprovedScopePicker template="scope_comparison" :documents="documents" :label="`Phạm vi B ý ${index+1}`" :model-value="row.scopeB" :disabled="disabled"
            @update:model-value="update(row.id,{scopeB:$event})" />
        </template>
        <template v-else>
          <label v-if="row.action==='where_used'">Mã chi tiết
            <input :value="row.part" :aria-label="`Mã chi tiết ý ${index+1}`" maxlength="250"
              @input="update(row.id,{part:($event.target as HTMLInputElement).value})" />
          </label>
          <label v-else>Bản vẽ
            <select :value="row.doc" :aria-label="`Bản vẽ ý ${index+1}`" @change="update(row.id,{doc:($event.target as HTMLSelectElement).value})">
              <option value="">Chọn bản vẽ</option>
              <option v-for="item in documents" :key="item.DocID" :value="String(item.DocID)">{{ [item.OriginalFileName?.trim() || `DocID ${item.DocID}`, item.DocNumber, item.VersionNo != null ? `v${item.VersionNo}` : ''].filter(Boolean).join(' · ') }}</option>
            </select>
          </label>
          <label v-if="row.action==='where_used'">Phạm vi sản phẩm
            <input :value="row.product" :aria-label="`Phạm vi sản phẩm ý ${index+1}`" maxlength="255"
              @input="update(row.id,{product:($event.target as HTMLInputElement).value})" />
          </label>
          <GraphScopePicker v-else :doc-id="row.doc" :site="row.site" :label="`Phạm vi sản phẩm ý ${index+1}`"
            :model-value="row.product" :disabled="disabled" @update:model-value="update(row.id,{product:$event})" />
          <label>Site
            <select :value="row.site" :aria-label="`Site ý ${index+1}`" @change="update(row.id,{site:($event.target as HTMLSelectElement).value})">
              <option value="">Chọn site</option><option v-for="site in auth.user?.allowed_sites" :key="site" :value="site">{{ site }}</option>
            </select>
          </label>
          <template v-if="row.action==='bom_rollup'">
            <label>Nguồn số lượng
              <select :value="row.quantityMode" :aria-label="`Nguồn số lượng ý ${index+1}`"
                @change="update(row.id,{quantityMode:($event.target as HTMLSelectElement).value as Draft['quantityMode']})">
                <option value="literal">Nhập số lượng</option><option value="source">Dùng số lượng từ nguồn đã duyệt</option>
              </select>
            </label>
            <label v-if="row.quantityMode==='literal'">Số lượng cần sản xuất
              <input :value="row.quantity" :aria-label="`Số lượng ý ${index+1}`" inputmode="decimal" maxlength="128"
                @input="update(row.id,{quantity:($event.target as HTMLInputElement).value})" />
            </label>
            <template v-else>
              <ApprovedScopePicker :documents="documents" :label="`Số lượng từ nguồn ý ${index+1}`" :model-value="row.quantityScope" :disabled="disabled"
                @update:model-value="update(row.id,{quantityScope:$event})" />
              <p>Nguồn phải có một dòng số lượng đã duyệt và liên kết đúng bản vẽ, phạm vi sản phẩm, site. Máy chủ kiểm tra khi gửi.</p>
            </template>
            <ApprovedScopePicker :documents="documents" :label="`Quy tắc số lượng ý ${index+1}`" :model-value="row.policy" :disabled="disabled"
              @update:model-value="update(row.id,{policy:$event})" />
          </template>
        </template>
        <label v-for="(previous,n) in rows.slice(0,index)" :key="previous.id" class="dependency">
          <input type="checkbox" :checked="row.dependencies.includes(previous.id)"
            @change="depend(row,previous.id,($event.target as HTMLInputElement).checked)" /> Ý này cần kết quả ý {{ n+1 }}
        </label>
        <button v-if="rows.length>1" type="button" @click="remove(row.id)">Bỏ ý {{ index+1 }}</button>
      </fieldset>
      <button v-if="!standalone" type="button" :disabled="disabled || loading || rows.length>=3 || (rows[0]?.action==='scope_total' && !totalQueryAvailable)" @click="rows=[...rows,draft()]">Thêm ý</button>
      <p v-if="!state.valid && !loading && !error" role="status">Chọn đủ nguồn và phạm vi cho từng ý trước khi gửi.</p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.advanced-actions { max-height:40vh; overflow:auto; border:1px solid var(--border); padding:.6rem; border-radius:6px; }
fieldset { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:.6rem; border:1px solid var(--border); margin:.5rem 0; }
label { display:grid; gap:.2rem; font-size:.9rem; }
label:has(input[type=checkbox]) { display:flex; align-items:center; gap:.4rem; }
input,select,button { background:var(--surface); color:var(--text); border:1px solid var(--border); padding:.4rem; border-radius:4px; min-width:0; }
button { cursor:pointer; } p { margin:.4rem 0; font-size:.85rem; color:var(--muted); }
</style>
