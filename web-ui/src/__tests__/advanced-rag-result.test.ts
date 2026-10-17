import { flushPromises, mount } from '@vue/test-utils';
import { expect, it, vi } from 'vitest';
import * as api from '@/api/client';
import AdvancedRagResult from '@/components/AdvancedRagResult.vue';
import type { AdvancedRagMetadata, Citation } from '@/types';

vi.mock('@/api/client',()=>({apiGet:vi.fn()}));


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
