import { afterEach, expect, it, vi } from 'vitest';
import { sendChatMessage } from '@/api/client';

afterEach(()=>vi.unstubAllGlobals());
it('passes progress on thinking/error and keeps done separate from snapshot completion',async()=>{
  const progress={search_mode:'comprehensive',scan_id:'a',continuation_cursor:'opaque',snapshot_complete:false};
  const body=[['thinking',{search_progress:progress}],['delta',{text:'Answer'}],
    ['done',{search_progress:{...progress,turn_complete:true}}]].map(([event,data])=>`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`).join('');
  const fetchMock=vi.fn().mockResolvedValue(new Response(body));vi.stubGlobal('fetch',fetchMock);
  const callbacks={onThinking:vi.fn(),onDelta:vi.fn(),onProgress:vi.fn(),onWarning:vi.fn(),onDone:vi.fn(),onError:vi.fn()};
  const controller=new AbortController();
  await sendChatMessage({search_mode:'comprehensive',continuation_cursor:'opaque'},callbacks,controller.signal);
  expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  expect(callbacks.onProgress).toHaveBeenCalledWith(progress);
  expect(callbacks.onDone).toHaveBeenCalledWith({search_progress:{...progress,turn_complete:true}});
  vi.mocked(fetch).mockResolvedValue(new Response('event: error\ndata: '+JSON.stringify({message:'No access',status:403,
    search_progress:{search_mode:'comprehensive',scan_status:'denied'}})+'\n\n'));
  await sendChatMessage({},callbacks);
  expect(callbacks.onError).toHaveBeenCalledWith('No access',expect.objectContaining({status:403}));
  expect(callbacks.onProgress).toHaveBeenLastCalledWith({search_mode:'comprehensive',scan_status:'denied'});
});
it('reports interrupted transport as technical error, not insufficient evidence',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('event: thinking\ndata: {}\n\n')));
  const callbacks={onThinking:vi.fn(),onDelta:vi.fn(),onWarning:vi.fn(),onDone:vi.fn(),onError:vi.fn()};
  await expect(sendChatMessage({},callbacks)).rejects.toThrow('chưa hoàn tất');
  expect(callbacks.onDone).not.toHaveBeenCalled();
});
