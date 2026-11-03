import { describe, expect, it } from 'vitest';
import { applyScanProgress, mergeScanResult } from '@/utils/chatScan';
import type { ChatMessage, SearchProgress } from '@/types';

const progress: SearchProgress = {search_mode:'comprehensive',scan_id:'a',scan_status:'pending',
  result_revision:1,continuation_cursor:'r1',snapshot_complete:false,turn_complete:true};
const first: ChatMessage = {role:'assistant',content:'First',search_progress:progress,
  citations:[{doc_id:1,page_no:1,version_no:1,evidence_identity:'v1',has_vision:false,original_url:'/v1'}]};
describe('scan delivery',()=>{
  it('accepts backend completion for an empty snapshot at revision zero',()=>{
    const result=mergeScanResult({role:'assistant',content:''} as ChatMessage,
      {search_progress:{...progress,result_revision:0,snapshot_complete:true}},'No sources in this snapshot');
    expect(result.search_progress?.snapshot_complete).toBe(true);
    expect(result.content).toContain('No sources');
  });
  it('keeps the consumed cursor when thinking reports newer committed work',()=>{
    expect(applyScanProgress(first,{...progress,continuation_cursor:'r3',result_revision:3}).search_progress?.continuation_cursor).toBe('r1');
    expect(applyScanProgress(first,{...progress,snapshot_complete:true,scan_status:'complete'}).search_progress?.snapshot_complete).toBe(false);
  });
  it('does not upgrade earlier partial or unverified results on a later verified revision',()=>{
    for (const verification_status of ['partial',undefined]) {
      const result=mergeScanResult({...first,verification_status},{verification_status:'verified',
        search_progress:{...progress,result_revision:2}},'Next');
      expect(result.verification_status).not.toBe('verified');
    }
  });
  it('merges consecutive revisions, keeps versions, and ignores replay',()=>{
    const done={search_progress:{...progress,result_revision:2,continuation_cursor:'r2'},citations:[
      ...first.citations!,{...first.citations![0],evidence_identity:'v2',version_no:2,original_url:'/v2'}]};
    const second=mergeScanResult(first,done,'Second');
    expect(second.content).toBe('First\n\nSecond');
    expect(second.citations).toHaveLength(2);
    expect(mergeScanResult(second,done,'Second')).toEqual(second);
  });
  it('rejects gaps and mismatched scans without consuming the cursor',()=>{
    expect(()=>mergeScanResult(first,{search_progress:{...progress,result_revision:3}},'Skipped')).toThrow();
    expect(()=>mergeScanResult(first,{search_progress:{...progress,scan_id:'b',result_revision:2}},'Other')).toThrow();
  });
  it('clears protected results and cursor on denied or stale',()=>{
    for (const scan_status of ['denied','stale'] as const) {
      const result=applyScanProgress(first,{search_mode:'comprehensive',scan_status,snapshot_complete:false});
      expect(result.content).toBe('');
      expect(result.citations).toEqual([]);
      expect(result.search_progress?.continuation_cursor).toBeUndefined();
    }
  });
});
