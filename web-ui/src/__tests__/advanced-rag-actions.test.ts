import { flushPromises, mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, expect, it, vi } from 'vitest';
import * as api from '@/api/client';
import { useAuthStore } from '@/stores/auth';
import AdvancedRagActions from '@/components/AdvancedRagActions.vue';
import type { QueryActionState, UserProfile } from '@/types';

vi.mock('@/api/client',()=>({apiGet:vi.fn()}));

it('selects reviewed BOM quantity without sending a stale literal and restores literal mode',async()=>{
  vi.mocked(api.apiGet).mockImplementation(async path=>{
    if(path.endsWith('evaluation-capabilities')) return capabilities as never;
    if(path.endsWith('/graph-scopes')) return {items:[{product_scope:'assembly-A',site:'factory'}],offset:0,has_more:false} as never;
    if(path==='/api/documents') return {documents:[{DocID:1,TenFile:'Cụm A.pdf'}]} as never;
    return {items:[{scope_id:'quantity',definition:'Đơn hàng',unit:'piece'},{scope_id:'policy',definition:'Quy tắc',unit:'piece'}],offset:0,has_more:false} as never;
  });
  const wrapper=create();await flushPromises();
  await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
  await wrapper.get('select[aria-label="Tác vụ ý 1"]').setValue('bom_rollup');
  await wrapper.get('select[aria-label="Bản vẽ ý 1"]').setValue('1');await flushPromises();
  await wrapper.get('select[aria-label="Phạm vi sản phẩm ý 1"]').setValue('assembly-A');
  await wrapper.get('select[aria-label="Tài liệu Quy tắc số lượng ý 1"]').setValue('1');await flushPromises();
  await wrapper.get('select[aria-label="Quy tắc số lượng ý 1"]').setValue('policy');
  const latest=()=>wrapper.emitted('change')!.at(-1)![0] as QueryActionState;
  expect(latest().query_plan!.branches[0].bom_rollup).toMatchObject({order_quantity:'1'});
  await wrapper.get('select[aria-label="Nguồn số lượng ý 1"]').setValue('source');
  expect(latest().valid).toBe(false);
  expect(wrapper.find('input[aria-label="Số lượng ý 1"]').exists()).toBe(false);
  await wrapper.get('select[aria-label="Tài liệu Số lượng từ nguồn ý 1"]').setValue('1');await flushPromises();
  await wrapper.get('select[aria-label="Số lượng từ nguồn ý 1"]').setValue('quantity');
  expect(latest().valid).toBe(true);
  expect(latest().query_plan!.branches[0].bom_rollup).toMatchObject({quantity_scope:'quantity',rounding_scope:'policy'});
  expect(latest().query_plan!.branches[0].bom_rollup).not.toHaveProperty('order_quantity');
  await wrapper.get('select[aria-label="Nguồn số lượng ý 1"]').setValue('literal');
  expect(latest().query_plan!.branches[0].bom_rollup).toMatchObject({order_quantity:'1'});
  expect(latest().query_plan!.branches[0].bom_rollup).not.toHaveProperty('quantity_scope');
  await wrapper.get('select[aria-label="Nguồn số lượng ý 1"]').setValue('source');
  expect(latest().valid).toBe(false);
});
const profile = {user_id:7,username:'approver',roles:['reviewer'],allowed_departments:['Technical'],
  allowed_sites:['factory'],max_security_level:'internal',csrf_token:'test'} as UserProfile;
const capabilities = {schema:'advanced-rag-v2-evaluation',accepted:false,offline_only:true,max_branches:3,
  actions:['contains','where_used','bom_rollup','scope_comparison']};
beforeEach(()=>{
  vi.clearAllMocks();
  vi.mocked(api.apiGet).mockImplementation(async path=>{
    if (path.endsWith('evaluation-capabilities')) return capabilities as never;
    if (path.endsWith('/graph-scopes')) return {items:[{product_scope:'assembly-A',site:'factory'}],offset:0,has_more:false} as never;
    if (path==='/api/documents') return {documents:[{DocID:1,TenFile:'Cụm A.pdf'}]} as never;
    return {items:[],has_more:false,offset:0} as never;
  });
});
function create(user:UserProfile|null=profile) {
  const pinia=createPinia();setActivePinia(pinia);useAuthStore().user=user;
  return mount(AdvancedRagActions,{props:{disabled:false},global:{plugins:[pinia]}});
}

it('sends a single reviewed scope total independently and disables adding branches',async()=>{
  vi.mocked(api.apiGet).mockImplementation(async path=>{
    if(path.endsWith('evaluation-capabilities')) return {...capabilities,actions:[...capabilities.actions,'scope_total']} as never;
    if(path==='/api/documents') return {documents:[{DocID:1,TenFile:'Chi phí.csv'}]} as never;
    return {items:[{scope_id:'total-a',definition:'Chi phí',unit:'VND'}],offset:0,has_more:false} as never;
  });
  const wrapper=create();await flushPromises();
  await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
  await wrapper.get('select[aria-label="Tác vụ ý 1"]').setValue('scope_total');
  await wrapper.get('select[aria-label="Tài liệu Phạm vi tính tổng ý 1"]').setValue('1');await flushPromises();
  await wrapper.get('select[aria-label="Phạm vi tính tổng ý 1"]').setValue('total-a');
  expect(wrapper.emitted('change')!.at(-1)).toEqual([{active:true,valid:true,scope_total:{scope_id:'total-a'}}]);
  expect(wrapper.findAll('button').find(button=>button.text()==='Thêm ý')!.attributes('disabled')).toBeDefined();
});

it('allows explicit total branches only when the server advertises that contract',async()=>{
  vi.mocked(api.apiGet).mockImplementation(async path=>{
    if(path.endsWith('evaluation-capabilities')) return {...capabilities,scope_total_query:true,actions:[...capabilities.actions,'scope_total']} as never;
    if(path==='/api/documents') return {documents:[{DocID:1,TenFile:'Chi phí.csv'}]} as never;
    return {items:[{scope_id:'total-a',definition:'Chi phí',unit:'VND'}],offset:0,has_more:false} as never;
  });
  const wrapper=create();await flushPromises();
  await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
  await wrapper.get('select[aria-label="Tác vụ ý 1"]').setValue('scope_total');
  await wrapper.get('select[aria-label="Tài liệu Phạm vi tính tổng ý 1"]').setValue('1');await flushPromises();
  await wrapper.get('select[aria-label="Phạm vi tính tổng ý 1"]').setValue('total-a');
  await wrapper.findAll('button').find(button=>button.text()==='Thêm ý')!.trigger('click');
  await wrapper.get('select[aria-label="Tác vụ ý 2"]').setValue('scope_total');
  await wrapper.get('select[aria-label="Tài liệu Phạm vi tính tổng ý 2"]').setValue('1');await flushPromises();
  await wrapper.get('select[aria-label="Phạm vi tính tổng ý 2"]').setValue('total-a');
  const state=wrapper.emitted('change')!.at(-1)![0] as QueryActionState;
  expect(state.valid).toBe(true);expect(state).not.toHaveProperty('scope_total');
  expect(state.query_plan!.branches.map(branch=>branch.scope_total)).toEqual([{scope_id:'total-a'},{scope_id:'total-a'}]);
});

it('offers only one scope total to a viewer with the exact live total contract',async()=>{
  vi.mocked(api.apiGet).mockImplementation(async path=>{
    if(path.endsWith('evaluation-capabilities')) return {schema:'advanced-rag-v2-controlled-demo',
      contract:'grounded-math-scope-total-controlled-demo-v1',accepted:false,offline_only:false,
      max_branches:1,actions:['scope_total'],text_query:false} as never;
    if(path==='/api/documents') return {documents:[{DocID:1,TenFile:'Chi phí.csv'}]} as never;
    return {items:[{scope_id:'total-a',definition:'Chi phí',unit:'VND'}],offset:0,has_more:false} as never;
  });
  const wrapper=create();
  useAuthStore().user={...useAuthStore().user!,roles:['viewer']};await flushPromises();
  expect(wrapper.text()).toContain('Tính tổng có căn cứ (bật thử)');
  await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
  expect(wrapper.get('select[aria-label="Tác vụ ý 1"]').findAll('option').map(option=>option.element.value)).toEqual(['scope_total']);
  expect(wrapper.findAll('button').some(button=>button.text()==='Thêm ý')).toBe(false);
  expect(wrapper.find('select[aria-label="Chế độ đánh giá"]').exists()).toBe(false);
  await wrapper.get('select[aria-label="Tài liệu Phạm vi tính tổng ý 1"]').setValue('1');await flushPromises();
  await wrapper.get('select[aria-label="Phạm vi tính tổng ý 1"]').setValue('total-a');
  expect(wrapper.emitted('change')!.at(-1)).toEqual([{active:true,valid:true,scope_total:{scope_id:'total-a'}}]);
});

it('switches between granted total and comparison without a stale scope or query branch',async()=>{
  vi.mocked(api.apiGet).mockImplementation(async path=>{
    if(path.endsWith('evaluation-capabilities')) return {schema:'advanced-rag-v2-controlled-demo',
      contract:'math-reviewed-templates-controlled-demo-v1',accepted:false,offline_only:false,
      max_branches:1,actions:['scope_comparison','scope_total'],text_query:false} as never;
    if(path==='/api/documents') return {documents:[{DocID:1,TenFile:'Chi phí.csv'}]} as never;
    return {items:[{scope_id:'a',definition:'Kỳ A',unit:'VND'},{scope_id:'b',definition:'Kỳ B',unit:'VND'}],offset:0,has_more:false} as never;
  });
  const wrapper=create();
  useAuthStore().user={...useAuthStore().user!,roles:['viewer']};await flushPromises();
  await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
  const action=wrapper.get('select[aria-label="Tác vụ ý 1"]');
  expect(action.findAll('option').map(option=>option.element.value)).toEqual(['scope_comparison','scope_total']);
  expect(wrapper.findAll('button').some(button=>button.text()==='Thêm ý')).toBe(false);
  await action.setValue('scope_total');
  await wrapper.get('select[aria-label="Tài liệu Phạm vi tính tổng ý 1"]').setValue('1');await flushPromises();
  await wrapper.get('select[aria-label="Phạm vi tính tổng ý 1"]').setValue('a');
  expect(wrapper.emitted('change')!.at(-1)).toEqual([{active:true,valid:true,scope_total:{scope_id:'a'}}]);
  await action.setValue('scope_comparison');await flushPromises();
  expect((wrapper.emitted('change')!.at(-1)![0] as QueryActionState).valid).toBe(false);
  for(const [label,id] of [['A','a'],['B','b']]) {
    await wrapper.get(`select[aria-label="Tài liệu Phạm vi ${label} ý 1"]`).setValue('1');await flushPromises();
    await wrapper.get(`select[aria-label="Phạm vi ${label} ý 1"]`).setValue(id);
  }
  expect(wrapper.emitted('change')!.at(-1)).toEqual([{active:true,valid:true,scope_comparison:{scope_a:'a',scope_b:'b'}}]);
});

it('builds an explicit bounded graph action and clears it when disabled',async()=>{
  const wrapper=create(); await flushPromises();
  await wrapper.get('input[type="checkbox"]').setValue(true); await flushPromises();
  await wrapper.get('select[aria-label="Bản vẽ ý 1"]').setValue('1');
  await flushPromises();await wrapper.get('select[aria-label="Phạm vi sản phẩm ý 1"]').setValue('assembly-A');
  const state=wrapper.emitted('change')!.at(-1)![0] as Record<string,unknown>;
  expect(state).toEqual({active:true,valid:true,query_plan:{branches:[{branch_id:'b1',question:'Cụm chứa gì?',
    graph_query:{root_key:'document:1',product_scope:'assembly-A',site:'factory',direction:'contains'},depends_on:[]}]}});
  await wrapper.get('select[aria-label="Bản vẽ ý 1"]').setValue('');await flushPromises();
  const cleared=wrapper.emitted('change')!.at(-1)![0] as QueryActionState;
  expect(cleared.valid).toBe(false);expect(cleared.query_plan!.branches[0].graph_query!.product_scope).toBe('');
  await wrapper.get('input[type="checkbox"]').setValue(false);
  expect(wrapper.emitted('change')!.at(-1)).toEqual([{active:false,valid:true}]);
});


it('selects server-advertised text evaluation without sending stale template choices',async()=>{
  vi.mocked(api.apiGet).mockResolvedValueOnce({...capabilities,text_query:true});
  const wrapper=create();await flushPromises();
  await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
  await wrapper.get('select[aria-label="Chế độ đánh giá"]').setValue('text');
  expect(wrapper.emitted('change')!.at(-1)).toEqual([{active:true,valid:true,text_query:true}]);
  expect(wrapper.find('fieldset').exists()).toBe(false);
  await wrapper.get('select[aria-label="Chế độ đánh giá"]').setValue('structured');
  expect(wrapper.find('fieldset').exists()).toBe(true);
  expect((wrapper.emitted('change')!.at(-1)![0] as QueryActionState).valid).toBe(false);
});

it('keeps the ordinary chat path for readers and production responses',async()=>{
  const reader=create({...profile,roles:['viewer']});await flushPromises();
  expect(reader.find('input').exists()).toBe(false);reader.unmount();
  vi.mocked(api.apiGet).mockRejectedValueOnce(new Error('HTTP 409'));
  const production=create();await flushPromises();expect(production.find('input').exists()).toBe(false);
});

it('resets old choices and reloads capabilities when the signed-in user changes',async()=>{
  const wrapper=create();await flushPromises();
  await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
  useAuthStore().user={...profile,user_id:9,username:'another'};await flushPromises();
  expect((wrapper.get('input[type="checkbox"]').element as HTMLInputElement).checked).toBe(false);
  expect(wrapper.find('fieldset').exists()).toBe(false);
  expect(wrapper.emitted('change')!.at(-1)).toEqual([{active:false,valid:true}]);
});

it('combines comparison, BOM and reverse graph without cutting or merging a fourth intent',async()=>{
  vi.mocked(api.apiGet).mockImplementation(async path=>{
    if (path.endsWith('evaluation-capabilities')) return capabilities as never;
    if (path.endsWith('/graph-scopes')) return {items:[{product_scope:'assembly-A',site:'factory'}],offset:0,has_more:false} as never;
    if (path==='/api/documents') return {documents:[{DocID:1,TenFile:'Cụm A.pdf'}]} as never;
    return {items:[{scope_id:'a',definition:'Kỳ A',period:'2026-06',unit:'VND'},
      {scope_id:'b',definition:'Kỳ B',period:'2026-07',unit:'VND'},
      {scope_id:'pieces',definition:'Số lượng',period:'',unit:'piece'}],has_more:false,offset:0} as never;
  });
  const wrapper=create();await flushPromises();
  await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
  await wrapper.get('select[aria-label="Tác vụ ý 1"]').setValue('scope_comparison');
  for (const [label,scope] of [['Phạm vi A ý 1','a'],['Phạm vi B ý 1','b']]) {
    await wrapper.get(`select[aria-label="Tài liệu ${label}"]`).setValue('1');await flushPromises();
    await wrapper.get(`select[aria-label="${label}"]`).setValue(scope);
  }
  const add=wrapper.findAll('button').find(b=>b.text()==='Thêm ý')!;
  await add.trigger('click');await wrapper.get('select[aria-label="Tác vụ ý 2"]').setValue('bom_rollup');
  await wrapper.get('select[aria-label="Bản vẽ ý 2"]').setValue('1');
  await flushPromises();await wrapper.get('select[aria-label="Phạm vi sản phẩm ý 2"]').setValue('assembly-A');
  await wrapper.get('input[aria-label="Số lượng ý 2"]').setValue('9007199254740993.01');
  await wrapper.get('select[aria-label="Tài liệu Quy tắc số lượng ý 2"]').setValue('1');await flushPromises();
  await wrapper.get('select[aria-label="Quy tắc số lượng ý 2"]').setValue('pieces');
  await wrapper.findAll('fieldset')[1].get('input[type="checkbox"]').setValue(true);
  await add.trigger('click');await wrapper.get('select[aria-label="Tác vụ ý 3"]').setValue('where_used');
  await wrapper.get('input[aria-label="Mã chi tiết ý 3"]').setValue('P-19');
  await wrapper.get('input[aria-label="Phạm vi sản phẩm ý 3"]').setValue('assembly-A');
  const state=wrapper.emitted('change')!.at(-1)![0] as QueryActionState;
  expect(state.valid).toBe(true);expect(state.query_plan!.branches).toHaveLength(3);
  expect(state.query_plan!.branches[0].scope_comparison).toEqual({scope_a:'a',scope_b:'b'});
  expect(state.query_plan!.branches[1].bom_rollup!.order_quantity).toBe('9007199254740993.01');
  expect(state.query_plan!.branches[1].depends_on).toEqual(['b1']);
  expect(state.query_plan!.branches[2].graph_query!.root_key).toBe('part:P-19');
  expect(add.attributes('disabled')).toBeDefined();
  await wrapper.findAll('button').find(b=>b.text()==='Bỏ ý 1')!.trigger('click');
  expect((wrapper.emitted('change')!.at(-1)![0] as QueryActionState).query_plan!.branches[0].depends_on).toEqual([]);
});

it('freezes source choices while a chat request is running and clears them on source load errors',async()=>{
  const wrapper=create();await flushPromises();
  await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
  await wrapper.get('select[aria-label="Bản vẽ ý 1"]').setValue('1');
  await flushPromises();await wrapper.get('select[aria-label="Phạm vi sản phẩm ý 1"]').setValue('assembly-A');
  await wrapper.setProps({disabled:true});
  const count=wrapper.emitted('change')!.length;
  await wrapper.get('select[aria-label="Phạm vi sản phẩm ý 1"]').setValue('');
  expect(wrapper.emitted('change')!).toHaveLength(count);
  expect(wrapper.get('fieldset').attributes('disabled')).toBeDefined();
  await wrapper.setProps({disabled:false});
  await wrapper.get('input[type="checkbox"]').setValue(false);
  vi.mocked(api.apiGet).mockRejectedValueOnce(new Error('secret scope'));
  await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
  expect(wrapper.text()).toContain('Không tải được danh sách');expect(wrapper.text()).not.toContain('secret scope');
  expect((wrapper.emitted('change')!.at(-1)![0] as QueryActionState).valid).toBe(false);
});
