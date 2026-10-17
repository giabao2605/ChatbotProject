import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, expect, it, vi } from 'vitest';
import * as api from '@/api/client';
import StructuredSourceFacts from '@/components/StructuredSourceFacts.vue';
import type { Citation } from '@/types';

vi.mock('@/api/client', () => ({ apiGet: vi.fn() }));
const citation = { doc_id:17, page_no:null, source_id:'D17L1234567890ABCDEF',
  scope_hash:'b'.repeat(64),
  citation_schema:'source-locator-v2', snapshot_id:'s1', scope_id:'a',
  has_vision:false, original_url:'/api/files/documents/17/original' } as Citation;
const fact = { fact_id:'f1', label:'Giá trị', raw_value:'9007199254740993.01',
  value:'9007199254740993.01', unit:'VND', locator:{doc_id:'17',version:'3',sheet:'Sổ',cell:'B2',row_key:'row-2'} };

beforeEach(() => vi.clearAllMocks());

it('opens a read-only paginated source table without rounding Decimal strings', async () => {
  vi.mocked(api.apiGet).mockResolvedValueOnce({items:[fact], snapshot_id:'s1',scope_hash:'b'.repeat(64),offset:0,has_more:true} as never)
    .mockResolvedValueOnce({items:[{...fact,fact_id:'f2',locator:{...fact.locator,cell:'B52'}}],snapshot_id:'s1',scope_hash:'b'.repeat(64),offset:50,has_more:false} as never);
  const wrapper = mount(StructuredSourceFacts, {props:{citation}});
  expect(api.apiGet).not.toHaveBeenCalled();
  await wrapper.get('button').trigger('click');
  await flushPromises();
  expect(wrapper.get('table').text()).toContain('9007199254740993.01');
  expect(wrapper.get('table').text()).toContain('B2');
  expect(wrapper.findAll('input,textarea')).toHaveLength(0);
  await wrapper.findAll('button').find(b=>b.text()==='Trang sau')!.trigger('click');
  await flushPromises();
  expect(api.apiGet).toHaveBeenLastCalledWith('/api/rag-evidence/approved-scopes/a/facts',{offset:50,limit:50});
  expect(wrapper.get('table').text()).toContain('B52');
});

it('hides values when the pinned snapshot no longer matches', async () => {
  vi.mocked(api.apiGet).mockResolvedValue({items:[fact],snapshot_id:'new-snapshot',offset:0,has_more:false} as never);
  const wrapper = mount(StructuredSourceFacts,{props:{citation}});
  await wrapper.get('button').trigger('click');
  await flushPromises();
  expect(wrapper.find('table').exists()).toBe(false);
  expect(wrapper.get('[role="alert"]').text()).toContain('Nguồn đã thay đổi');
});

it('hides changed reviewed values even when the raw snapshot is unchanged', async () => {
  vi.mocked(api.apiGet).mockResolvedValue({items:[fact],snapshot_id:'s1',scope_hash:'c'.repeat(64),offset:0,has_more:false} as never);
  const wrapper = mount(StructuredSourceFacts,{props:{citation}});
  await wrapper.get('button').trigger('click');
  await flushPromises();
  expect(wrapper.find('table').exists()).toBe(false);
  expect(wrapper.get('[role="alert"]').text()).toContain('Nguồn đã thay đổi');
});

it('clears denied data and allows a safe retry', async () => {
  vi.mocked(api.apiGet).mockRejectedValueOnce(new Error('private source'))
    .mockResolvedValueOnce({items:[],snapshot_id:'s1',scope_hash:'b'.repeat(64),offset:0,has_more:false} as never);
  const wrapper = mount(StructuredSourceFacts,{props:{citation}});
  await wrapper.get('button').trigger('click');
  await flushPromises();
  expect(wrapper.text()).not.toContain('private source');
  expect(wrapper.get('[role="alert"]').text()).toContain('Không tải được');
  await wrapper.get('button').trigger('click');
  await flushPromises();
  expect(wrapper.text()).toContain('Không có dòng dữ liệu');
  expect(wrapper.find('[role="alert"]').exists()).toBe(false);
});

it('discards a pending page when the selected citation changes', async () => {
  let resolvePage!: (value:unknown)=>void;
  vi.mocked(api.apiGet).mockReturnValueOnce(new Promise(resolve=>{ resolvePage=resolve; }) as never);
  const wrapper = mount(StructuredSourceFacts,{props:{citation}});
  await wrapper.get('button').trigger('click');
  expect(wrapper.get('[role="status"]').text()).toContain('Đang tải');
  await wrapper.setProps({citation:{...citation,source_id:'other-source',scope_id:'b'}});
  resolvePage({items:[fact],snapshot_id:'s1',scope_hash:'b'.repeat(64),offset:0,has_more:true});
  await flushPromises();
  expect(wrapper.find('table').exists()).toBe(false);
  expect(wrapper.text()).not.toContain('9007199254740993.01');
  wrapper.unmount();
});
