<script setup lang="ts">
import StructuredSourceFacts from '@/components/StructuredSourceFacts.vue';
import type { AdvancedRagMetadata } from '@/types';

defineProps<{result:AdvancedRagMetadata}>();
const emit=defineEmits<{unavailable:[]}>();
const statuses:Record<string,string>={complete:'Đầy đủ',partial:'Một phần',unknown:'Chưa xác định',blocked:'Chưa đủ dữ liệu'};
const evidenceStatuses:Record<string,string>={full_answer:'Đủ căn cứ truy xuất',partial_answer:'Căn cứ một phần',
  insufficient_evidence:'Chưa đủ căn cứ',access_denied:'Nguồn không khả dụng'};
</script>

<template>
  <section v-if="result.text_query?.schema==='query-text-v2'" class="branch-results" aria-label="Chứng cứ từng ý">
    <h3>Chứng cứ từng ý</h3>
    <p>Đây là trạng thái truy xuất của từng ý; câu trả lời được sinh chung phía trên. Kết quả đánh giá chưa được nghiệm thu phát hành.</p>
    <article v-for="(branch,index) in result.text_query.branches" :key="branch.branch_id">
      <h3>Ý {{ index+1 }}: {{ branch.subquery }}</h3>
      <p>{{ evidenceStatuses[branch.outcome] || 'Chưa xác định' }}</p>
      <p v-if="!branch.cited_sources.length">Chưa có nguồn được dẫn trong câu trả lời cho ý này.</p>
      <div v-for="source in branch.cited_sources" :key="source.source_id || source.original_url">
        <a :href="source.original_url" target="_blank" rel="noopener">{{ source.file_name || `Tài liệu ${source.doc_id}` }}</a>
      </div>
    </article>
  </section>
  <section v-if="result.query?.schema==='query-plan-v2'" class="branch-results" aria-label="Kết quả từng ý">
    <p v-if="result.query.offline_only">Kết quả đánh giá, chưa nghiệm thu phát hành.</p>
    <article v-for="(branch,index) in result.query.branches" :key="branch.branch_id">
      <h3>Ý {{ index+1 }}: {{ branch.question }}</h3>
      <p>Trạng thái: {{ statuses[branch.status] || statuses.unknown }}</p>
      <p v-if="branch.graph?.source_status">Cây nguồn: {{ statuses[branch.graph.source_status] || statuses.unknown }}</p>
      <div v-if="branch.math?.steps?.length" class="math-table">
        <table><thead><tr><th>Bước tính</th><th>Kết quả</th><th>Đơn vị</th></tr></thead>
          <tbody><tr v-for="step in branch.math.steps" :key="step.step_id">
            <td>{{ step.formula }}</td><td>{{ step.status==='undefined'?'Không xác định':(step.display_value ?? step.value) }}</td><td>{{ step.unit }}</td>
          </tr></tbody>
        </table>
      </div>
      <details v-if="branch.sources.length"><summary>Nguồn của ý {{ index+1 }}</summary>
        <div v-for="source in branch.sources" :key="source.source_id || source.original_url">
          <a :href="source.original_url" target="_blank" rel="noopener">{{ source.file_name || `Tài liệu ${source.doc_id}` }}</a>
          <StructuredSourceFacts v-if="source.scope_id" :citation="source" @unavailable="emit('unavailable')" />
        </div>
      </details>
    </article>
  </section>
</template>

<style scoped>
.branch-results { border-top:1px solid var(--border); margin-top:.6rem; font-size:.9rem; }
article { border-bottom:1px solid var(--border); padding:.4rem 0; } h3 { font-size:.95rem; }
p { margin:.4rem 0; } .math-table { overflow-x:auto; }
table { width:100%; border-collapse:collapse; } th,td { text-align:left; padding:.4rem; border:1px solid var(--border); }
</style>
