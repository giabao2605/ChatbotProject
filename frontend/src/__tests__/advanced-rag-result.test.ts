import { flushPromises, mount } from '@vue/test-utils';
import { expect, it, vi } from 'vitest';
import * as api from '@/api/client';
import AdvancedRagResult from '@/components/AdvancedRagResult.vue';
import type { AdvancedRagMetadata, Citation } from '@/types';

vi.mock('@/api/client',()=>({apiGet:vi.fn()}));

it('renders live mixed branch results and keeps partial status without an offline label',()=>{
  const wrapper=mount(AdvancedRagResult,{props:{result:{query:{schema:'query-plan-mixed-v1-draft',
    contract:'query-reviewed-actions-controlled-demo-v1',activation_scope:'controlled_demo',
    status:'partial',accepted:false,offline_only:false,branches:[
      {branch_id:'sum',question:'Tổng',status:'complete',sources:[],math:{steps:[{step_id:'sum',formula:'sum(a)',value:'1500',unit:'VND'}]}},
      {branch_id:'graph',question:'Quan hệ',status:'partial',sources:[],graph:{source_status:'partial'}}]}}}});
  expect(wrapper.findAll('article')).toHaveLength(2);
  expect(wrapper.text()).toContain('Một phần');
  expect(wrapper.text()).not.toContain('Kết quả đánh giá');
  expect(wrapper.get('table').text()).toContain('1500');
});

it('renders total query branches with exact raw values under the new schema',()=>{
  const wrapper=mount(AdvancedRagResult,{props:{result:{query:{schema:'query-plan-scope-total-v1',
    status:'complete',accepted:false,offline_only:true,branches:[{branch_id:'s',question:'Tổng A',
      status:'complete',sources:[],math:{schema:'grounded-calculation-scope-total-v1',steps:[
        {step_id:'sum',formula:'sum(a)',value:'2.345',display_value:'2.35',unit:'VND'}]}}]}}}});
  expect(wrapper.text()).toContain('Tổng A');expect(wrapper.text()).toContain('2.345');
  expect(wrapper.get('table').text()).toContain('2.35');
});

it('renders a scope total with exact values and invalidates its changed source',async()=>{
  const source:Citation={doc_id:17,page_no:null,has_vision:false,source_id:'D17L1234567890abcdef',scope_id:'s',
    snapshot_id:'snapshot',scope_hash:'b'.repeat(64),citation_schema:'source-locator-v2',
    original_url:'/api/files/documents/17/original?scope_id=s'};
  const wrapper=mount(AdvancedRagResult,{props:{result:{math:{schema:'grounded-calculation-scope-total-v1',
    template:'sum_reviewed_scope',status:'complete',accepted:false,offline_only:true,steps:[
      {step_id:'sum',formula:'sum(s)',value:'9007199254740993.015',display_value:'9007199254740993.02',unit:'VND'}],sources:[source]}}}});
  expect(wrapper.text()).toContain('9007199254740993.015');
  expect(wrapper.text()).toContain('9007199254740993.02');
  expect(wrapper.text()).toContain('sum(s)');
  expect(wrapper.get('a').attributes('href')).toBe(source.original_url);
  vi.mocked(api.apiGet).mockResolvedValue({items:[],scope_hash:'c'.repeat(64),snapshot_id:'snapshot',offset:0,has_more:false});
  await wrapper.get('button').trigger('click');await flushPromises();
  expect(wrapper.emitted('unavailable')).toHaveLength(1);
});


it('shows text branch evidence separately from the shared answer and only resolved links',()=>{
  const source={doc_id:17,page_no:2,source_id:'D17P2',file_name:'policy.pdf',has_vision:false,
    original_url:'/api/files/documents/17/original'} as Citation;
  const wrapper=mount(AdvancedRagResult,{props:{result:{text_query:{schema:'query-text-v2',accepted:false,offline_only:true,
    branches:[{branch_id:'branch-1',subquery:'Quy trình nghỉ phép',outcome:'full_answer',cited_sources:[source]},
      {branch_id:'branch-2',subquery:'Hướng dẫn đào tạo',outcome:'insufficient_evidence',cited_sources:[]}]}}}});
  expect(wrapper.text()).toContain('Chứng cứ từng ý');
  expect(wrapper.text()).toContain('Đủ căn cứ truy xuất');
  expect(wrapper.text()).toContain('Chưa đủ căn cứ');
  expect(wrapper.findAll('a')).toHaveLength(1);
  expect(wrapper.get('a').attributes('href')).toBe(source.original_url);
});

it('renders exact Decimal step strings and undefined ratios with sources for each branch',async()=>{
  const source:Citation={doc_id:17,page_no:null,source_id:'D17L1234567890ABCDEF',scope_id:'a',
    snapshot_id:'s1',scope_hash:'b'.repeat(64),citation_schema:'source-locator-v2',has_vision:false,
    original_url:'/api/files/documents/17/original?scope_id=a'};
  const result:AdvancedRagMetadata={query:{schema:'query-plan-v2',status:'partial',accepted:false,offline_only:true,
    branches:[{branch_id:'b1',question:'So sánh hai kỳ',status:'partial',sources:[source],math:{steps:[
      {step_id:'sum_a',formula:'SUM(A)',display_value:'0.00',unit:'VND'},
      {step_id:'sum_b',formula:'SUM(B)',display_value:'9007199254740993.01',unit:'VND'},
      {step_id:'percent_change',formula:'(B-A)/A*100',value:null,status:'undefined',unit:'%'}]}},
      {branch_id:'b2',question:'Tra cây cụm',status:'unknown',sources:[],graph:{source_status:'unknown'}},
      {branch_id:'b3',question:'Ý phụ thuộc',status:'blocked',sources:[]}]}};
  const wrapper=mount(AdvancedRagResult,{props:{result}});
  expect(wrapper.get('table').text()).toContain('9007199254740993.01');
  expect(wrapper.get('table').text()).toContain('Không xác định');
  expect(wrapper.findAll('article')).toHaveLength(3);
  expect(wrapper.text()).toContain('Chưa đủ dữ liệu');
  expect(wrapper.get('a').attributes('href')).toBe(source.original_url);
  vi.mocked(api.apiGet).mockResolvedValue({items:[],scope_hash:'c'.repeat(64),snapshot_id:'s1',offset:0,has_more:false});
  await wrapper.get('button').trigger('click');await flushPromises();
  expect(wrapper.emitted('unavailable')).toHaveLength(1);
});

it('hides unversioned metadata and escapes source-defined text',async()=>{
  const wrapper=mount(AdvancedRagResult,{props:{result:{}}});
  expect(wrapper.find('section').exists()).toBe(false);
  await wrapper.setProps({result:{query:{schema:'query-plan-v2',status:'complete',accepted:false,offline_only:false,
    branches:[{branch_id:'b1',question:'<script>unsafe</script>',status:'unexpected',sources:[],math:{steps:[
      {step_id:'x',formula:'<img src=x>',value:'123.00',unit:'piece'}]},graph:{source_status:'unexpected'}}]}}});
  expect(wrapper.find('script,img').exists()).toBe(false);
  expect(wrapper.text()).toContain('<script>unsafe</script>');
  expect(wrapper.get('table').text()).toContain('123.00');
  expect(wrapper.text()).toContain('Chưa xác định');
});
