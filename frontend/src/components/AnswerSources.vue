<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import StructuredSourceFacts from '@/components/StructuredSourceFacts.vue';
import type { ChatMessage, Citation } from '@/types';

const props = defineProps<{ message:ChatMessage }>();
const emit = defineEmits<{ unavailable:[] }>();
const failedPreviews = ref<string[]>([]);
const error = ref('');
const opening = ref(false);
let controller:AbortController|undefined;
const unknown = 'Chưa xác nhận';
const relationships:Record<string,string> = {consensus:'Đồng thuận',different_scope:'Khác phạm vi',
  conflict:'Mâu thuẫn',replacement:'Có căn cứ thay thế'};
const cards = computed(() => {
  const groups = props.message.source_groups ?? [];
  const citations = props.message.citations ?? [];
  const matches = (group:typeof groups[number],citation:Citation) => group.evidence_identities?.length
    ? Boolean(citation.evidence_identity && group.evidence_identities.includes(citation.evidence_identity))
    : group.evidence_identity ? group.evidence_identity === citation.evidence_identity
    : group.source_id === citation.source_id && String(group.doc_id) === String(citation.doc_id) &&
      String(group.version_no ?? '') === String(citation.version_no ?? '');
  const entries = citations.map(citation => ({citation, groups:groups.filter(group => matches(group,citation) &&
    (group.evidence_identities?.length || citations.filter(candidate=>matches(group,candidate)).length === 1))}));
  return [...entries, ...groups.filter(group => !entries.some(entry=>entry.groups.includes(group)))
    .map(group=>({citation:undefined,groups:[group]}))];
});

function sourceLocation(citation?:Citation) {
  if (!citation) return unknown;
  const locator=citation.locator;
  return [locator?.page || citation.page_no ? `Trang ${locator?.page || citation.page_no}` : '',
    locator?.sheet ? `Sheet ${locator.sheet}` : '',locator?.cell ? `Ô ${locator.cell}` : '',
    locator?.row_key ? `Dòng ${locator.row_key}` : ''].filter(Boolean).join(' · ') || unknown;
}

function safeUrl(value?:string|null) {
  if (!value) return undefined;
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin && /^\/api\/files\/documents\/\d+\/(original|pages\/\d+)$/.test(url.pathname)
      ? value : undefined;
  } catch { return undefined; }
}

async function openSource(value:string,fileName?:string) {
  if (opening.value || !safeUrl(value)) return;
  error.value='';
  const tab = fileName ? null : window.open('about:blank','_blank');
  if (!fileName && !tab) { error.value='Trình duyệt đã chặn mở nguồn. Hãy cho phép mở cửa sổ và thử lại.';return; }
  if (tab) tab.opener=null;
  opening.value=true;
  const request = new AbortController();
  controller=request;
  try {
    const response=await fetch(value,{credentials:'include',cache:'no-store',redirect:'error',signal:request.signal});
    request.signal.throwIfAborted();
    if (!response.ok) {
      if ([401,403,404,410].includes(response.status)) {
        error.value='Nguồn không còn khả dụng hoặc bạn không còn quyền xem. Hãy hỏi lại để xác minh nguồn.';
        emit('unavailable');
      } else error.value='Không tải được nguồn do lỗi kỹ thuật. Vui lòng thử lại.';
      tab?.close();return;
    }
    const blob=await response.blob();
    request.signal.throwIfAborted();
    const url=URL.createObjectURL(blob);
    if (fileName) {
      const download=document.createElement('a');
      download.href=url;download.download=fileName;download.click();
    } else if (tab) tab.location.href=url;
    // Blob URL must outlive navigation in the new tab.
    window.setTimeout(()=>URL.revokeObjectURL(url),60_000);
  } catch {
    tab?.close();
    if (!request.signal.aborted) error.value='Không tải được nguồn do lỗi kỹ thuật. Vui lòng thử lại.';
  } finally { if (controller === request) opening.value=false; }
}

watch(()=>props.message,()=>{controller?.abort();opening.value=false;error.value='';failedPreviews.value=[];});
onBeforeUnmount(()=>controller?.abort());
</script>

<template>
  <section v-if="cards.length" class="answer-sources" aria-label="Tài liệu làm căn cứ" :aria-busy="opening">
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-for="(batch,batchIndex) in [cards.slice(0,3),cards.slice(3)]" :key="batchIndex">
      <component :is="batchIndex ? 'details' : 'div'" v-if="batch.length" class="source-results">
        <summary v-if="batchIndex">Xem thêm {{ batch.length }} nguồn đã tìm thấy</summary>
        <div class="citation-grid">
          <article v-for="(card,index) in batch" :key="card.citation?.evidence_identity || `${batchIndex}-${index}`" class="citation-card"
            :class="{'structured-citation':card.citation?.citation_schema === 'source-locator-v2'}">
            <a v-if="card.citation && safeUrl(card.citation.page_url) && !/\.(?:md|markdown)$/i.test(card.citation.file_name || '') && !failedPreviews.includes(card.citation.page_url!)"
              :href="card.citation.page_url!" target="_blank" rel="noopener" class="citation-preview-link"
              :aria-label="`Mở ảnh nguồn ${card.citation.file_name || card.citation.doc_id}, ${sourceLocation(card.citation)}`"
              @click.prevent="openSource(card.citation.page_url!)">
              <img :src="card.citation.page_url!" :alt="`Nguồn ${card.citation.file_name || card.citation.doc_id}, ${sourceLocation(card.citation)}`"
                @error="failedPreviews = [...failedPreviews,card.citation.page_url!]" />
            </a>
            <div class="citation-card-body">
              <p class="source-title">{{ card.citation?.file_name || `Tài liệu ${card.citation?.doc_id ?? card.groups[0]?.doc_id}` }}
                · Phiên bản {{ card.citation?.version_no ?? card.groups[0]?.version_no ?? unknown }}</p>
              <template v-if="card.groups.length">
                <div v-for="(group,groupIndex) in card.groups" :key="groupIndex">
                  <p v-if="(group.evidence_identities?.length ?? 0) > 1">Nội dung đã xác minh theo trang; đối chiếu từng đoạn tại nguồn.</p>
                  <p v-for="(text,textIndex) in group.related_texts" :key="textIndex" class="source-passage">{{ text }}</p>
                  <dl v-for="(claim,claimIndex) in group.claims" :key="claimIndex">
                    <dt>Đối tượng</dt><dd>{{ claim.subject || unknown }}</dd>
                    <dt>Điều kiện</dt><dd>{{ claim.condition || unknown }}</dd>
                    <dt>Thời gian</dt><dd>{{ claim.time || unknown }}</dd>
                    <dt>Quan hệ giữa nguồn</dt><dd>{{ relationships[claim.relationship] || unknown }}</dd>
                  </dl>
                  <p v-if="!group.claims?.length">Đối tượng, điều kiện, thời gian: {{ unknown }}</p>
                </div>
              </template>
              <p v-else>Nội dung liên quan, đối tượng, điều kiện, thời gian: {{ unknown }}</p>
              <p>Trạng thái áp dụng: {{ unknown }}</p>
              <span>Vị trí: {{ sourceLocation(card.citation) }}</span>
              <p v-if="card.citation?.locator?.sheet || card.citation?.locator?.cell || card.citation?.locator?.row_key">Vị trí dùng để đối chiếu trong bản gốc; trình xem không tự định vị ô hoặc dòng.</p>
              <template v-if="card.citation">
                <p v-if="/\.(?:md|markdown)$/i.test(card.citation.file_name || '')">Tài liệu văn bản không có ảnh trang. Mở bản gốc để đối chiếu.</p>
                <p v-else-if="card.citation.page_url && failedPreviews.includes(card.citation.page_url)">Không tải được ảnh xem trước. Bạn có thể mở bản gốc để đối chiếu.</p>
                <p v-else-if="!safeUrl(card.citation.page_url)">Không có ảnh xem trước. Bạn có thể mở bản gốc để đối chiếu.</p>
                <a v-if="safeUrl(card.citation.original_url)" :href="card.citation.original_url" target="_blank" rel="noopener"
                  class="citation-download" :aria-label="`Tải bản gốc ${card.citation.file_name || card.citation.doc_id}, phiên bản ${card.citation.version_no ?? unknown}`"
                  @click.prevent="openSource(card.citation.original_url,card.citation.file_name || `Tai-lieu-${card.citation.doc_id}`)">Tải bản gốc</a>
                <p v-else>Đường dẫn nguồn chưa được xác nhận.</p>
                <StructuredSourceFacts v-if="card.citation.citation_schema === 'source-locator-v2'" :citation="card.citation" @unavailable="emit('unavailable')" />
              </template>
              <p v-else>Chưa có đường dẫn xác nhận đúng phiên bản nguồn.</p>
            </div>
          </article>
        </div>
      </component>
    </template>
  </section>
</template>

<style scoped>
.citation-grid { grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr)); }
.structured-citation { grid-column:1 / -1; }
.source-title { font-weight:600; }
.source-passage { white-space:pre-wrap;overflow-wrap:anywhere; }
p { margin:0; }
dl { display:grid;grid-template-columns:auto 1fr;gap:4px 8px;margin:0; }
dt { color:var(--muted); } dd { margin:0;overflow-wrap:anywhere; }
summary { cursor:pointer;min-height:44px;display:list-item;padding:12px 0; }
a:focus-visible,summary:focus-visible { outline:2px solid var(--accent);outline-offset:3px; }
[role="alert"] { padding:8px 0; }
</style>
