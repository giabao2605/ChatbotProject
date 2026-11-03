import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, expect, it, vi } from 'vitest';
import AnswerSources from '@/components/AnswerSources.vue';
import type { ChatMessage, Citation } from '@/types';

vi.mock('@/api/client', () => ({ apiGet:vi.fn() }));
const citation = (version:number, identity:string):Citation => ({doc_id:7,version_no:version,page_no:2,
  source_id:'D7P2',evidence_identity:identity,file_name:'Quy định.pdf',has_vision:false,
  original_url:`/api/files/documents/7/original?version=${version}&snapshot_id=${identity}`,
  page_url:`/api/files/documents/7/pages/2?version=${version}&snapshot_id=${identity}`});
const source = (version:number, text:string, identity:string) => ({source_id:'D7P2',doc_id:7,version_no:version,evidence_identity:identity,
  related_texts:[text],claims:[]});
const message = (extra:Record<string,unknown> = {}) => ({role:'assistant',content:'Trả lời',...extra}) as ChatMessage;
beforeEach(() => {
  vi.clearAllMocks();
  vi.stubGlobal('fetch',vi.fn());
  vi.spyOn(window,'open').mockReturnValue({opener:null,location:{href:''},close:vi.fn()} as unknown as Window);
  URL.createObjectURL=vi.fn().mockReturnValue('blob:test');
  URL.revokeObjectURL=vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});
});

it('retains same document/page with distinct versions and passages, and only labels backend relationships', () => {
  const wrapper = mount(AnswerSources,{props:{message:message({citations:[citation(1,'old'),citation(2,'new')],
    source_groups:[source(1,'Đoạn bản cũ','old'),{...source(2,'Đoạn bản mới','new'),claims:[{text:'Đúng phạm vi',
      quotes:[],relationship:'different_scope',subject:'Xưởng A',condition:'Khi vận hành',time:'2026'}]}]})}});
  expect(wrapper.findAll('article')).toHaveLength(2);
  expect(wrapper.findAll('article')[0].text()).toContain('Đoạn bản cũ');
  expect(wrapper.findAll('article')[1].text()).toContain('Đoạn bản mới');
  expect(wrapper.text()).toContain('Khác phạm vi');
  expect(wrapper.text()).toContain('Xưởng A');
  expect(wrapper.text()).toContain('Chưa xác nhận');
  expect(wrapper.text()).not.toContain('Đang áp dụng');
});

it('keeps all valid passages and exposes further results in native keyboard-accessible details', async () => {
  const citations = [citation(1,'a'),citation(1,'b'),citation(2,'c'),citation(3,'d')];
  const wrapper=mount(AnswerSources,{props:{message:message({citations,source_groups:[source(1,'Đoạn A','a'),
    source(1,'Đoạn B','b'),source(2,'Đoạn C','c'),source(3,'Đoạn D','d')]})}});
  expect(wrapper.findAll('article')).toHaveLength(4);
  expect(wrapper.get('details > summary').text()).toContain('Xem thêm');
  expect(wrapper.get('details').findAll('article')).toHaveLength(1);
  expect(wrapper.text()).toContain('Đoạn A');expect(wrapper.text()).toContain('Đoạn B');
  expect(wrapper.findAll('article')[0].text()).not.toContain('Đoạn B');
  expect(wrapper.findAll('article')[1].text()).not.toContain('Đoạn A');
  expect(wrapper.findAll('a').every(link=>Boolean(link.attributes('aria-label')))).toBe(true);
});

it('retains every explicitly pinned chunk while marking multi-chunk group text as page-level', () => {
  const wrapper=mount(AnswerSources,{props:{message:message({citations:[citation(1,'a'),citation(1,'b')],
    source_groups:[{...source(1,'Đã đối chiếu cả trang',''),evidence_identity:undefined,evidence_identities:['a','b']}]})}});
  expect(wrapper.findAll('article')).toHaveLength(2);
  expect(wrapper.findAll('article').every(card=>card.text().includes('Nội dung đã xác minh theo trang'))).toBe(true);
  expect(wrapper.findAll('a.citation-download').map(link=>link.attributes('href')))
    .toEqual([citation(1,'a').original_url,citation(1,'b').original_url]);
});

it('opens the exact pinned page or original and reports unavailable without substituting another URL', async () => {
  vi.mocked(fetch).mockResolvedValueOnce({ok:true,blob:async()=>new Blob(['source'])} as Response)
    .mockResolvedValueOnce({ok:false,status:403} as Response);
  const pinned=citation(1,'old');
  const wrapper=mount(AnswerSources,{props:{message:message({citations:[pinned]})}});
  const original=wrapper.get('a.citation-download');
  expect(original.attributes('href')).toBe(pinned.original_url);
  await original.trigger('click');await flushPromises();
  expect(fetch).toHaveBeenLastCalledWith(pinned.original_url,expect.objectContaining({credentials:'include',cache:'no-store',redirect:'error'}));
  expect(HTMLAnchorElement.prototype.click).toHaveBeenCalledTimes(1);
  expect(window.open).not.toHaveBeenCalled();
  await wrapper.get('a.citation-preview-link').trigger('click');await flushPromises();
  expect(fetch).toHaveBeenLastCalledWith(pinned.page_url,expect.objectContaining({credentials:'include',cache:'no-store',redirect:'error'}));
  expect(wrapper.get('[role="alert"]').text()).toContain('không còn');
  expect(wrapper.emitted('unavailable')).toHaveLength(1);
});

it('keeps legacy citations safe and avoids claiming page positioning for sheet or cell locations', () => {
  const wrapper=mount(AnswerSources,{props:{message:message({citations:[{...citation(1,'a'),page_no:null,
    page_url:null,version_no:null,locator:{doc_id:'7',version:'1',sheet:'Sổ',cell:'B2'}}]})}});
  expect(wrapper.text()).toContain('Ô B2');
  expect(wrapper.text()).toContain('Vị trí dùng để đối chiếu');
  expect(wrapper.text()).not.toContain('trang null');
  expect(wrapper.text()).toContain('Chưa xác nhận');
});

it('reports a failed preview clearly without invalidating evidence or emitting a replacement link', async () => {
  const wrapper=mount(AnswerSources,{props:{message:message({citations:[citation(1,'a')]})}});
  await wrapper.get('img').trigger('error');
  expect(wrapper.text()).toContain('Không tải được ảnh xem trước');
  expect(wrapper.emitted('unavailable')).toBeUndefined();
  expect(wrapper.get('a.citation-download').attributes('href')).toBe(citation(1,'a').original_url);
});

it('blocks external and review-preview URLs and does not assign ambiguous groups to a pinned citation', () => {
  const wrapper=mount(AnswerSources,{props:{message:message({citations:[{...citation(1,'a'),
    original_url:'https://other.invalid/api/files/documents/7/original',page_url:'/api/files/documents/7/review-preview'},
    citation(1,'b')],source_groups:[{...source(1,'Không đoán snapshot',''),evidence_identity:undefined}]})}});
  expect(wrapper.findAll('a')).toHaveLength(2);
  expect(wrapper.findAll('a').every(link=>!link.attributes('href')?.includes('other.invalid') &&
    !link.attributes('href')?.includes('review-preview'))).toBe(true);
  expect(wrapper.findAll('article')[0].text()).not.toContain('Không đoán snapshot');
  expect(wrapper.text()).toContain('Chưa có đường dẫn xác nhận đúng phiên bản nguồn');
});

it('aborts a source request when the message changes and does not open its stale content', async () => {
  let resolve!: (response:Response)=>void;
  vi.mocked(fetch).mockReturnValue(new Promise(done=>{resolve=done;}));
  const wrapper=mount(AnswerSources,{props:{message:message({citations:[citation(1,'a')]})}});
  await wrapper.get('a.citation-preview-link').trigger('click');
  const signal=vi.mocked(fetch).mock.calls[0][1]?.signal;
  await wrapper.setProps({message:message({citations:[]})});
  expect(signal?.aborted).toBe(true);
  resolve({ok:true,blob:async()=>new Blob(['old'])} as Response);await flushPromises();
  expect(URL.createObjectURL).not.toHaveBeenCalled();
  expect(wrapper.emitted('unavailable')).toBeUndefined();
});
