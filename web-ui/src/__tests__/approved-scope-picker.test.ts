import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, expect, it, vi } from 'vitest';
import * as api from '@/api/client';
import ApprovedScopePicker from '@/components/ApprovedScopePicker.vue';

vi.mock('@/api/client', () => ({apiGet:vi.fn()}));
const documents = [{DocID:1,TenFile:'Bảng kê.xlsx'}, {DocID:2,TenFile:'Kỳ sau.csv'}];
const first = {scope_id:'a',definition:'Doanh thu',period:'Tháng 6',unit:'VND',currency:'VND'};
beforeEach(()=>vi.clearAllMocks());

it('selects a reviewed scope by business definition and pages without numeric conversion', async () => {
  vi.mocked(api.apiGet).mockResolvedValueOnce({items:[first],offset:0,limit:50,has_more:true})
    .mockResolvedValueOnce({items:[{...first,scope_id:'b',period:'Tháng 7'}],offset:50,limit:50,has_more:false});
  const wrapper = mount(ApprovedScopePicker,{props:{label:'Phạm vi A',documents,modelValue:''}});
  await wrapper.get('select[aria-label="Tài liệu Phạm vi A"]').setValue('1');
  await flushPromises();
  expect(wrapper.text()).toContain('Doanh thu · Tháng 6 · VND');
  await wrapper.get('select[aria-label="Phạm vi A"]').setValue('a');
  expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['a']);
  await wrapper.findAll('button').find(b=>b.text()==='Trang sau')!.trigger('click');
  await flushPromises();
  expect(api.apiGet).toHaveBeenLastCalledWith('/api/rag-evidence/documents/1/approved-scopes',{offset:50,limit:50});
  expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['']);
  expect(wrapper.text()).toContain('Tháng 7');
});

it('discards a late page after changing source and fails closed on a failed page', async () => {
  let resolveOld!:(value:unknown)=>void;
  vi.mocked(api.apiGet).mockImplementationOnce(()=>new Promise(resolve=>{resolveOld=resolve;}) as never)
    .mockRejectedValueOnce(new Error('restricted detail'));
  const wrapper = mount(ApprovedScopePicker,{props:{label:'Phạm vi A',documents,modelValue:'a'}});
  await wrapper.get('select[aria-label="Tài liệu Phạm vi A"]').setValue('1');
  await wrapper.get('select[aria-label="Tài liệu Phạm vi A"]').setValue('2');
  await flushPromises();
  resolveOld({items:[first],offset:0,limit:50,has_more:false});
  await flushPromises();
  expect(wrapper.text()).not.toContain('Doanh thu');
  expect(wrapper.text()).not.toContain('restricted detail');
  expect(wrapper.text()).toContain('Không tải được phạm vi đã duyệt');
  expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['']);
});

it('loads scopes for the selected template and clears the old selection on a template change',async()=>{
  vi.mocked(api.apiGet).mockResolvedValue({items:[first],offset:0,limit:50,has_more:false});
  const wrapper=mount(ApprovedScopePicker,{props:{label:'Phạm vi',documents,modelValue:'a',template:'scope_total'}});
  await wrapper.get('select[aria-label="Tài liệu Phạm vi"]').setValue('1');await flushPromises();
  expect(api.apiGet).toHaveBeenLastCalledWith('/api/rag-evidence/documents/1/approved-scopes',
    {offset:0,limit:50,template:'scope_total'});
  await wrapper.setProps({template:'scope_comparison'});await flushPromises();
  expect(api.apiGet).toHaveBeenLastCalledWith('/api/rag-evidence/documents/1/approved-scopes',
    {offset:0,limit:50,template:'scope_comparison'});
  expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['']);
});
