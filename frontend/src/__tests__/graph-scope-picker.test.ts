import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, expect, it, vi } from 'vitest';
import { apiGet } from '@/api/client';
import GraphScopePicker from '@/components/GraphScopePicker.vue';

vi.mock('@/api/client',()=>({apiGet:vi.fn()}));
beforeEach(()=>vi.resetAllMocks());
const choice={product_scope:'product-A',site:'factory'};
function create(props={}) {
  return mount(GraphScopePicker,{props:{docId:'1',site:'factory',label:'Phạm vi sản phẩm ý 1',modelValue:'',...props}});
}

it('requires explicit choice from approved scopes and clears it on document or site change',async()=>{
  vi.mocked(apiGet).mockResolvedValue({items:[choice,{...choice,product_scope:'product-B'}],offset:0,has_more:false});
  const wrapper=create();await flushPromises();
  expect(apiGet).toHaveBeenCalledWith('/api/rag-evidence/documents/1/graph-scopes',{site:'factory',offset:0,limit:50});
  expect(wrapper.emitted('update:modelValue')).toEqual([['']]);
  await wrapper.get('select').setValue('product-B');
  expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['product-B']);
  await wrapper.setProps({docId:'2',modelValue:'product-B'});await flushPromises();
  expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['']);
  await wrapper.setProps({site:'other'});await flushPromises();
  expect(wrapper.findAll('option')).toHaveLength(1);
  await wrapper.setProps({docId:''});await flushPromises();
  expect(wrapper.text()).toContain('Chọn bản vẽ và site');
});

it('pages through visible choices without keeping selection from the previous page',async()=>{
  vi.mocked(apiGet).mockResolvedValueOnce({items:[choice],offset:0,has_more:true})
    .mockResolvedValueOnce({items:[{...choice,product_scope:'product-Z'}],offset:50,has_more:false})
    .mockResolvedValueOnce({items:[choice],offset:0,has_more:true});
  const wrapper=create();await flushPromises();
  await wrapper.get('select').setValue('product-A');
  await wrapper.findAll('button')[1].trigger('click');await flushPromises();
  expect(apiGet).toHaveBeenLastCalledWith('/api/rag-evidence/documents/1/graph-scopes',{site:'factory',offset:50,limit:50});
  expect(wrapper.emitted('update:modelValue')!.at(-1)).toEqual(['']);
  expect(wrapper.findAll('button')[1].attributes('disabled')).toBeDefined();
  await wrapper.findAll('button')[0].trigger('click');await flushPromises();
  expect(wrapper.text()).toContain('product-A');
});

it('discards late responses from an old source and from an unmounted picker',async()=>{
  let finish:(value:unknown)=>void=()=>{};
  vi.mocked(apiGet).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}) as never)
    .mockResolvedValueOnce({items:[{...choice,product_scope:'current'}],offset:0,has_more:false});
  const wrapper=create();await wrapper.setProps({docId:'2'});await flushPromises();
  finish({items:[choice],offset:0,has_more:true});await flushPromises();
  expect(wrapper.text()).toContain('current');expect(wrapper.text()).not.toContain('product-A');
  vi.mocked(apiGet).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}) as never);
  await wrapper.setProps({docId:'3'});wrapper.unmount();
  finish({items:[choice],offset:0,has_more:false});await flushPromises();
});

it('keeps failed or missing scopes invalid, hides server errors and offers retry',async()=>{
  vi.mocked(apiGet).mockRejectedValueOnce(new Error('restricted product-name'))
    .mockResolvedValueOnce({items:[],offset:0,has_more:false});
  const wrapper=create();await flushPromises();
  expect(wrapper.text()).toContain('Không tải được');expect(wrapper.text()).not.toContain('restricted product-name');
  await wrapper.findAll('button').find(button=>button.text()==='Thử lại')!.trigger('click');await flushPromises();
  expect(wrapper.text()).toContain('Chưa có phạm vi');expect(wrapper.get('select').attributes('disabled')).toBeDefined();
});

it('freezes selection while sending and does not load without a document and site',async()=>{
  vi.mocked(apiGet).mockResolvedValue({items:[choice],offset:0,has_more:false});
  const wrapper=create({disabled:true});await flushPromises();
  const count=wrapper.emitted('update:modelValue')!.length;
  await wrapper.get('select').setValue('product-A');
  expect(wrapper.emitted('update:modelValue')).toHaveLength(count);
  expect(wrapper.findAll('button').every(button=>button.attributes('disabled')!==undefined)).toBe(true);
  wrapper.unmount();vi.mocked(apiGet).mockClear();
  create({site:''});await flushPromises();expect(apiGet).not.toHaveBeenCalled();
});
