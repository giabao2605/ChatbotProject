import type { ChatMessage, Citation, SearchProgress } from '@/types';

export function citationKey(source: Citation): string {
  return source.evidence_identity || JSON.stringify([source.doc_id,source.version_no,
    source.snapshot_id,source.source_hash,source.locator,source.page_no,source.source_id,source.original_url]);
}

export function applyScanProgress<T extends ChatMessage>(message:T, progress:SearchProgress):T {
  if (progress.scan_status === 'stale' || progress.scan_status === 'denied') {
    return {...message,content:'',citations:[],source_groups:[],ref_text:'',advanced_rag:undefined,
      verification_status:undefined,answer_outcome:undefined,
      search_progress:{search_mode:progress.search_mode,scan_status:progress.scan_status,snapshot_complete:false}};
  }
  const previous=message.search_progress;
  if (previous?.scan_id && progress.scan_id && previous.scan_id !== progress.scan_id) {
    throw new Error('Lần rà đã thay đổi. Hãy bắt đầu lại.');
  }
  // Thinking can be ahead of delivered results after a disconnect; only done advances the cursor.
  return {...message,search_progress:{...previous,...progress,
    continuation_cursor:previous?.continuation_cursor ?? progress.continuation_cursor,
    result_revision:previous?.result_revision,
    snapshot_complete:previous?.snapshot_complete ?? false,
    results_pending:previous?.results_pending ?? false}};
}

export function mergeScanResult<T extends ChatMessage>(message:T, data:Partial<ChatMessage>, text:string):T {
  const progress=data.search_progress;
  if (!progress || progress.search_mode !== 'comprehensive') return {...message,...data,content:text};
  const checked=applyScanProgress(message,progress);
  if (progress.scan_status === 'stale' || progress.scan_status === 'denied') return checked;
  const previous=message.search_progress?.result_revision ?? 0;
  const revision=progress.result_revision;
  if (revision === undefined) return {...checked,...data,content:message.content || text};
  if (revision <= previous && message.search_progress?.result_revision !== undefined) return message;
  if (revision !== previous+1 && !(revision===0 && previous===0)) throw new Error('Chưa nhận đủ kết quả đã lưu. Hãy tiếp tục từ lượt trước.');
  const citations=[...(message.citations ?? []),...(data.citations ?? [])];
  const keys=new Set<string>();
  const statuses=[message.verification_status,data.verification_status];
  const verification=!message.content ? data.verification_status :
    statuses.every(status=>status==='verified') ? 'verified' :
    statuses.some(status=>!status || status==='unverified') ? 'unverified' : 'partial';
  return {...message,...data,content:[message.content,text].filter(Boolean).join('\n\n'),
    verification_status:verification,
    answer_outcome:message.content && message.answer_outcome !== data.answer_outcome ? 'partial_answer' : data.answer_outcome,
    citations:citations.filter(source=>{const key=citationKey(source);if(keys.has(key)) return false;keys.add(key);return true;}),
    source_groups:[...(message.source_groups ?? []),...(data.source_groups ?? [])]};
}
