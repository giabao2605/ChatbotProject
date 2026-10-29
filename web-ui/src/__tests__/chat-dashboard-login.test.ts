import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { defineComponent, h, nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import { useChatStore } from "@/stores/chat";
import ChatView from "@/views/ChatView.vue";
import DashboardView from "@/views/DashboardView.vue";
import LoginView from "@/views/LoginView.vue";
import type { ChatMessage, UserProfile } from "@/types";

const routerState = vi.hoisted(() => ({
  push: vi.fn(),
  query: {} as Record<string, unknown>,
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: routerState.push }),
  useRoute: () => ({ query: routerState.query }),
}));

vi.mock("@/api/client", () => ({
  apiGet: vi.fn(),
  deleteSession: vi.fn(),
  listSessions: vi.fn(),
  loadDashboard: vi.fn(),
  loadHistory: vi.fn(),
  login: vi.fn(),
  loginErrorMessage: (error: unknown) => {
    const message = error instanceof Error ? error.message : "Đăng nhập thất bại";
    return /<(?:!doctype|html|head|body|title|h1)\b/i.test(message)
      ? "Dịch vụ đăng nhập đang tạm thời không khả dụng. Vui lòng thử lại."
      : message;
  },
  sendChatMessage: vi.fn(),
  sendFeedback: vi.fn(),
  uploadChatImage: vi.fn(),
}));

const ButtonStub = defineComponent({
  name: "Button",
  inheritAttrs: false,
  props: {
    label: { type: String, default: "" },
    disabled: Boolean,
    loading: Boolean,
  },
  emits: ["click"],
  setup(props, { attrs, emit, slots }) {
    return () =>
      h(
        "button",
        {
          ...attrs,
          disabled: props.disabled,
          "data-loading": String(props.loading),
          onClick: () => emit("click"),
        },
        [props.label, slots.default?.()],
      );
  },
});

const ModelInputStub = defineComponent({
  inheritAttrs: false,
  props: { modelValue: { type: String, default: "" } },
  emits: ["update:modelValue"],
  setup(props, { attrs, emit }) {
    return () =>
      h("input", {
        ...attrs,
        value: props.modelValue,
        onInput: (event: Event) =>
          emit("update:modelValue", (event.target as HTMLInputElement).value),
      });
  },
});

const TextareaStub = defineComponent({
  inheritAttrs: false,
  props: { modelValue: { type: String, default: "" } },
  emits: ["update:modelValue"],
  setup(props, { attrs, emit }) {
    return () =>
      h("textarea", {
        ...attrs,
        value: props.modelValue,
        onInput: (event: Event) =>
          emit("update:modelValue", (event.target as HTMLTextAreaElement).value),
      });
  },
});

const CardStub = defineComponent({
  inheritAttrs: false,
  setup(_props, { attrs, slots }) {
    return () =>
      h("article", { ...attrs, class: ["card", attrs.class] }, [
        slots.title?.(),
        slots.subtitle?.(),
        slots.content?.(),
        slots.default?.(),
      ]);
  },
});

const global = {
  stubs: {
    Button: ButtonStub,
    Card: CardStub,
    InputText: ModelInputStub,
    Message: { template: '<div class="message"><slot /></div>' },
    Password: ModelInputStub,
    ProgressSpinner: { template: '<div class="spinner">loading</div>' },
    Tag: {
      props: ["value"],
      template: '<span class="tag">{{ value }}</span>',
    },
    Textarea: TextareaStub,
  },
};

const user: UserProfile = {
  user_id: 7,
  username: "bao",
  display_name: "Bao",
  department: "Engineering",
  roles: ["viewer"],
  allowed_departments: ["Engineering"],
  max_security_level: "Internal",
  allowed_sites: ["HCM"],
  csrf_token: "csrf",
};

function newPinia() {
  const pinia = createPinia();
  setActivePinia(pinia);
  return pinia;
}

function mountWithPinia(component: typeof ChatView | typeof DashboardView | typeof LoginView) {
  const pinia = newPinia();
  return {
    pinia,
    wrapper: mount(component, {
      global: {
        ...global,
        plugins: [pinia],
      },
    }),
  };
}

function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll("button").find((item) => item.text() === label);
  expect(found, `button "${label}"`).toBeDefined();
  return found!;
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  routerState.query = {};
  vi.mocked(api.listSessions).mockResolvedValue([]);
  vi.mocked(api.loadHistory).mockResolvedValue([]);
  vi.mocked(api.deleteSession).mockResolvedValue(undefined);
  vi.mocked(api.sendFeedback).mockResolvedValue(undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ChatView public behavior", () => {
  it('opens Markdown originals without misleading previews and preserves pinned PDF originals on image failure',async()=>{
    const mdOriginal='/api/files/documents/1/original?citation_version=2&source_hash=abc';
    const pdfOriginal='/api/files/documents/2/original?citation_version=3&source_hash=def';
    vi.mocked(api.listSessions).mockResolvedValue([{session_id:'citations',cau_hoi:'Sources'}]);
    vi.mocked(api.loadHistory).mockResolvedValueOnce([{role:'assistant',content:'Answer',citations:[
      {doc_id:1,page_no:1,file_name:'POLICY.MARKDOWN',has_vision:true,page_url:'/markdown-image',original_url:mdOriginal},
      {doc_id:2,page_no:4,file_name:'manual.pdf',has_vision:true,page_url:'/pdf-image',original_url:pdfOriginal},
    ]}]);
    const {wrapper}=mountWithPinia(ChatView);await flushPromises();
    await button(wrapper,'Lịch sử').trigger('click');await wrapper.get('.history-title').trigger('click');await flushPromises();
    expect(wrapper.find('img[src="/markdown-image"]').exists()).toBe(false);
    expect(wrapper.text()).toContain('Tài liệu văn bản không có ảnh trang. Mở bản gốc để đối chiếu.');
    const pdf=wrapper.get('img[src="/pdf-image"]');await pdf.trigger('error');
    expect(wrapper.find('img[src="/pdf-image"]').exists()).toBe(false);
    expect(wrapper.text()).toContain('Không tải được ảnh xem trước. Bạn có thể mở bản gốc để đối chiếu.');
    expect(wrapper.findAll('.citation-download').map(link=>link.attributes('href'))).toEqual([mdOriginal,pdfOriginal]);
    wrapper.unmount();
  });

  it('restores the active conversation on remount before sending with its history and memory',async()=>{
    const history:ChatMessage[]=[{role:'user',content:'Quy trình IT'},{role:'assistant',content:'Các bước IT',chat_id:1}];
    vi.mocked(api.listSessions).mockResolvedValue([{session_id:'a',cau_hoi:'Quy trình IT'}]);
    vi.mocked(api.loadHistory).mockResolvedValueOnce(history);
    const {wrapper,pinia}=mountWithPinia(ChatView);await flushPromises();
    await button(wrapper,'Lịch sử').trigger('click');await wrapper.get('.history-title').trigger('click');await flushPromises();
    const chat=useChatStore(pinia);
    chat.updateMemory('a',{chatId:1,currentPartIds:['IT-1'],conversationContext:{topic:'IT'}});
    wrapper.unmount();
    let finish!:(value:ChatMessage[])=>void;
    vi.mocked(api.loadHistory).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    const restored=mount(ChatView,{global:{...global,plugins:[pinia]}});await flushPromises();
    expect(api.loadHistory).toHaveBeenLastCalledWith('a');
    expect(restored.text()).toContain('Đang tải lịch sử');
    expect(restored.text()).not.toContain('Xin chào');
    await restored.get('textarea').setValue('Ai duyệt?');
    expect(button(restored,'Gửi').attributes('disabled')).toBeDefined();
    await restored.get('textarea').trigger('keydown',{key:'Enter'});
    expect(api.sendChatMessage).not.toHaveBeenCalled();
    finish(history);await flushPromises();
    expect(restored.text()).toContain('Các bước IT');
    vi.mocked(api.sendChatMessage).mockResolvedValueOnce(undefined);
    await button(restored,'Gửi').trigger('click');await flushPromises();
    expect(api.sendChatMessage).toHaveBeenLastCalledWith(expect.objectContaining({
      session_id:'a',chat_history:history.map(({role,content})=>({role,content})),current_part_ids:['IT-1'],conversation_context:{topic:'IT'},
    }),expect.anything());
    restored.unmount();
  });

  it.each([null,99])('discards memory from a turn absent from restored nonempty history (chat id %s)',async chatId=>{
    const saved:ChatMessage[]=[{role:'user',content:'Old topic'}, {role:'assistant',content:'Saved old answer',chat_id:10}];
    vi.mocked(api.listSessions).mockResolvedValue([{session_id:'a',cau_hoi:'Old topic'}]);
    vi.mocked(api.loadHistory).mockResolvedValue(saved);
    vi.mocked(api.sendChatMessage).mockImplementationOnce(async(_payload,callbacks)=>{
      callbacks.onDelta('New unsaved topic');
      callbacks.onDone({chat_id:chatId,new_part_ids:['UNSAVED'],conversation_context:{topic:'unsaved'}});
    });
    const {wrapper,pinia}=mountWithPinia(ChatView);await flushPromises();
    await button(wrapper,'Lịch sử').trigger('click');await wrapper.get('.history-title').trigger('click');await flushPromises();
    await wrapper.get('textarea').setValue('Switch topic');await button(wrapper,'Gửi').trigger('click');await flushPromises();
    wrapper.unmount();
    const restored=mount(ChatView,{global:{...global,plugins:[pinia]}});await flushPromises();
    expect(restored.text()).toContain('Saved old answer');expect(restored.text()).not.toContain('New unsaved topic');
    expect(useChatStore(pinia).currentMemory).toEqual({currentPartIds:[],conversationContext:null});
    restored.unmount();
  });

  it('clears hidden anchors when the restored session has no saved messages',async()=>{
    const {wrapper,pinia}=mountWithPinia(ChatView);await flushPromises();
    const chat=useChatStore(pinia);chat.updateMemory(chat.sessionId,{currentPartIds:['old'],conversationContext:{topic:'hidden'}});
    wrapper.unmount();
    vi.mocked(api.loadHistory).mockResolvedValueOnce([]);
    const restored=mount(ChatView,{global:{...global,plugins:[pinia]}});await flushPromises();
    expect(chat.currentMemory).toEqual({currentPartIds:[],conversationContext:null});
    expect(restored.text()).toContain('Xin chào');restored.unmount();
  });

  it('keeps sending blocked after history failure until retry succeeds',async()=>{
    vi.mocked(api.listSessions).mockResolvedValue([{session_id:'a',cau_hoi:'A'}]);
    vi.mocked(api.loadHistory).mockRejectedValueOnce(new Error('private detail'))
      .mockResolvedValueOnce([{role:'assistant',content:'Restored answer'}]);
    const {wrapper}=mountWithPinia(ChatView);await flushPromises();
    await button(wrapper,'Lịch sử').trigger('click');await wrapper.get('.history-title').trigger('click');await flushPromises();
    await wrapper.get('textarea').setValue('Next question');
    expect(button(wrapper,'Gửi').attributes('disabled')).toBeDefined();
    await wrapper.get('textarea').trigger('keydown',{key:'Enter'});
    expect(api.sendChatMessage).not.toHaveBeenCalled();
    await button(wrapper,'Thử lại hội thoại').trigger('click');await flushPromises();
    expect(wrapper.text()).toContain('Restored answer');
    expect(button(wrapper,'Gửi').attributes('disabled')).toBeUndefined();
    wrapper.unmount();
  });

  it('waits for an in-flight conversation before restoring after remount and ignores detached UI callbacks',async()=>{
    let handlers!:Parameters<typeof api.sendChatMessage>[1];let finish!:()=>void;
    vi.mocked(api.sendChatMessage).mockImplementation((_payload,callbacks)=>{
      handlers=callbacks;return new Promise(resolve=>{finish=resolve;});
    });
    const {wrapper,pinia}=mountWithPinia(ChatView);await flushPromises();
    await wrapper.get('textarea').setValue('Question still processing');await button(wrapper,'Gửi').trigger('click');await flushPromises();
    const id=useChatStore(pinia).sessionId;wrapper.unmount();
    const restored=mount(ChatView,{global:{...global,plugins:[pinia]}});await flushPromises();
    expect(api.loadHistory).not.toHaveBeenCalled();
    await restored.get('textarea').setValue('Follow-up');
    expect(button(restored,'Gửi').attributes('disabled')).toBeDefined();
    vi.mocked(api.loadHistory).mockResolvedValueOnce([{role:'user',content:'Question still processing'},{role:'assistant',content:'Saved answer',chat_id:2}]);
    handlers.onDelta('Detached answer');handlers.onDone({chat_id:2,new_part_ids:['NEW'],conversation_context:{topic:'current'}});
    finish();await flushPromises();
    expect(api.loadHistory).toHaveBeenLastCalledWith(id);
    expect(restored.text()).toContain('Saved answer');expect(restored.text()).not.toContain('Detached answer');
    expect(useChatStore(pinia).currentMemory.currentPartIds).toEqual(['NEW']);
    restored.unmount();
  });

  it.each(['contains','scope_comparison'])('sends %s with the shared Math and Graph grant as a standalone action',async action=>{
    vi.mocked(api.apiGet).mockImplementation(async path=>{
      if(path.endsWith('evaluation-capabilities'))return {schema:'advanced-rag-v2-controlled-demo',
        contract:'math-graph-v2-controlled-demo-v1',accepted:false,offline_only:false,max_branches:1,
        actions:['contains','where_used','scope_comparison'],text_query:false} as never;
      if(path.endsWith('/graph-scopes'))return {items:[{product_scope:'assembly-A',site:'factory'}],offset:0,has_more:false} as never;
      if(path.endsWith('/approved-scopes'))return {items:[{scope_id:'a',definition:'Quantity',period:'A',unit:'piece'},
        {scope_id:'b',definition:'Quantity',period:'B',unit:'piece'}],offset:0,has_more:false} as never;
      return {documents:[{DocID:1,OriginalFileName:'Source.csv'}]} as never;
    });
    vi.mocked(api.sendChatMessage).mockImplementation(async(_payload,callbacks)=>{callbacks.onDelta('Kết quả có nguồn');callbacks.onDone({});});
    const {wrapper,pinia}=mountWithPinia(ChatView);
    useAuthStore(pinia).user={...user,roles:['viewer'],allowed_sites:['factory']};await flushPromises();
    await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
    expect(wrapper.get('select[aria-label="Tác vụ ý 1"]').findAll('option').map(item=>item.element.value)).toEqual(['contains','where_used','scope_comparison']);
    expect(wrapper.findAll('button').some(item=>item.text()==='Thêm ý')).toBe(false);
    await wrapper.get('select[aria-label="Tác vụ ý 1"]').setValue(action);await flushPromises();
    if(action==='contains'){
      await wrapper.get('select[aria-label="Bản vẽ ý 1"]').setValue('1');await flushPromises();
      await wrapper.get('select[aria-label="Phạm vi sản phẩm ý 1"]').setValue('assembly-A');
    }else{
      await wrapper.get('select[aria-label="Tài liệu Phạm vi A ý 1"]').setValue('1');await flushPromises();
      await wrapper.get('select[aria-label="Phạm vi A ý 1"]').setValue('a');
      await wrapper.get('select[aria-label="Tài liệu Phạm vi B ý 1"]').setValue('1');await flushPromises();
      await wrapper.get('select[aria-label="Phạm vi B ý 1"]').setValue('b');
    }
    await wrapper.get('textarea').setValue('Tác vụ đã chọn');await button(wrapper,'Gửi').trigger('click');await flushPromises();
    const payload=vi.mocked(api.sendChatMessage).mock.calls[0][0];
    if(action==='contains'){
      expect(payload.graph_query).toMatchObject({root_key:'document:1',product_scope:'assembly-A'});
      expect(payload).not.toHaveProperty('scope_comparison');
    }else{
      expect(payload.scope_comparison).toEqual({scope_a:'a',scope_b:'b'});expect(payload).not.toHaveProperty('graph_query');
    }
    expect(payload).not.toHaveProperty('query_plan');expect(payload).not.toHaveProperty('bom_rollup');expect(payload).not.toHaveProperty('text_query');
  });
  it('sends a granted standalone Graph query without enabling Math or Query',async()=>{
    vi.mocked(api.apiGet).mockImplementation(async path=>{
      if(path.endsWith('evaluation-capabilities')) return {schema:'advanced-rag-v2-controlled-demo',
        contract:'graph-v2-reviewed-query-controlled-demo-v1',accepted:false,offline_only:false,
        max_branches:1,actions:['contains','where_used'],text_query:false} as never;
      if(path.endsWith('/graph-scopes')) return {items:[{product_scope:'assembly-A',site:'factory'}],offset:0,has_more:false} as never;
      return {documents:[{DocID:1,OriginalFileName:'Assembly.pdf'}]} as never;
    });
    vi.mocked(api.sendChatMessage).mockImplementation(async(_payload,callbacks)=>{callbacks.onDelta('Quan hệ có nguồn');callbacks.onDone({});});
    const {wrapper,pinia}=mountWithPinia(ChatView);
    useAuthStore(pinia).user={...user,roles:['viewer'],allowed_sites:['factory']}; await flushPromises();
    await wrapper.get('input[type="checkbox"]').setValue(true); await flushPromises();
    expect(wrapper.get('select[aria-label="Tác vụ ý 1"]').findAll('option').map(item=>item.element.value)).toEqual(['contains','where_used']);
    expect(wrapper.findAll('button').some(item=>item.text()==='Thêm ý')).toBe(false);
    await wrapper.get('select[aria-label="Bản vẽ ý 1"]').setValue('1');await flushPromises();
    await wrapper.get('select[aria-label="Phạm vi sản phẩm ý 1"]').setValue('assembly-A');
    await wrapper.get('textarea').setValue('Cụm này có những chi tiết nào?');
    await button(wrapper,'Gửi').trigger('click');await flushPromises();
    const payload=vi.mocked(api.sendChatMessage).mock.calls[0][0];
    expect(payload.graph_query).toEqual({root_key:'document:1',product_scope:'assembly-A',site:'factory',direction:'contains'});
    expect(payload).not.toHaveProperty('query_plan');expect(payload).not.toHaveProperty('scope_comparison');
    expect(payload).not.toHaveProperty('bom_rollup');expect(payload).not.toHaveProperty('text_query');
  });
  it('sends a granted standalone comparison without opening Query or Graph', async () => {
    vi.mocked(api.apiGet).mockImplementation(async path => {
      if (path.endsWith('evaluation-capabilities')) return {schema:'advanced-rag-v2-controlled-demo',
        contract:'grounded-math-v2-comparison-pilot-v1',accepted:false,offline_only:false,
        max_branches:1,actions:['scope_comparison'],text_query:false} as never;
      if (path.endsWith('/approved-scopes')) return {items:[
        {scope_id:'a',definition:'Doanh thu',period:'Kỳ A',unit:'VND'},
        {scope_id:'b',definition:'Doanh thu',period:'Kỳ B',unit:'VND'}],offset:0,has_more:false} as never;
      return {documents:[{DocID:1,OriginalFileName:'Doanh thu.xlsx'}]} as never;
    });
    vi.mocked(api.sendChatMessage).mockImplementation(async (_payload,callbacks)=>{
      callbacks.onDelta('Kết quả so sánh có căn cứ'); callbacks.onDone({});
    });
    const {wrapper,pinia}=mountWithPinia(ChatView);
    useAuthStore(pinia).user={...user,roles:['viewer']}; await flushPromises();
    await wrapper.get('input[type="checkbox"]').setValue(true); await flushPromises();
    expect(wrapper.get('select[aria-label="Tác vụ ý 1"]').findAll('option').map(item=>item.element.value)).toEqual(['scope_comparison']);
    expect(wrapper.findAll('button').some(item=>item.text()==='Thêm ý')).toBe(false);
    expect(wrapper.find('select[aria-label="Chế độ đánh giá"]').exists()).toBe(false);
    await wrapper.get('select[aria-label="Tài liệu Phạm vi A ý 1"]').setValue('1'); await flushPromises();
    await wrapper.get('select[aria-label="Phạm vi A ý 1"]').setValue('a');
    await wrapper.get('select[aria-label="Tài liệu Phạm vi B ý 1"]').setValue('1'); await flushPromises();
    await wrapper.get('select[aria-label="Phạm vi B ý 1"]').setValue('b');
    await wrapper.get('textarea').setValue('So sánh hai kỳ đã chọn');
    await button(wrapper,'Gửi').trigger('click'); await flushPromises();
    const payload=vi.mocked(api.sendChatMessage).mock.calls[0][0];
    expect(payload.scope_comparison).toEqual({scope_a:'a',scope_b:'b'});
    expect(payload).not.toHaveProperty('query_plan'); expect(payload).not.toHaveProperty('text_query');
    expect(payload).not.toHaveProperty('graph_query'); expect(payload).not.toHaveProperty('bom_rollup');
  });
  it('sends text evaluation without a query plan and renders per-intent evidence',async()=>{
    vi.mocked(api.apiGet).mockImplementation(async path=>path.endsWith('evaluation-capabilities')?
      {schema:'advanced-rag-v2-evaluation',accepted:false,offline_only:true,max_branches:3,text_query:true,
       actions:['contains','where_used','bom_rollup','scope_comparison']} as never : {documents:[]} as never);
    vi.mocked(api.sendChatMessage).mockImplementation(async (_payload,callbacks)=>{
      callbacks.onDelta('Câu trả lời chung có căn cứ.');
      callbacks.onDone({advanced_rag:{text_query:{schema:'query-text-v2',accepted:false,offline_only:true,
        branches:[{branch_id:'branch-1',subquery:'Quy trình nghỉ phép',outcome:'full_answer',cited_sources:[]},
          {branch_id:'branch-2',subquery:'Đào tạo',outcome:'insufficient_evidence',cited_sources:[]}]}}});
    });
    const {wrapper,pinia}=mountWithPinia(ChatView);
    useAuthStore(pinia).user={...user,roles:['reviewer']};await flushPromises();
    await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
    await wrapper.get('select[aria-label="Chế độ đánh giá"]').setValue('text');
    await wrapper.get('textarea').setValue('Quy trình nghỉ phép; đào tạo');
    await button(wrapper,'Gửi').trigger('click');await flushPromises();
    const payload=vi.mocked(api.sendChatMessage).mock.calls[0][0];
    expect(payload.text_query).toBe(true);expect(payload).not.toHaveProperty('query_plan');
    expect(wrapper.text()).toContain('Chứng cứ từng ý');
    expect(wrapper.text()).toContain('Chưa đủ căn cứ');
  });
  it('sends selected actions through chat and keeps per-branch results', async () => {
    vi.mocked(api.apiGet).mockImplementation(async path => {
      if (path.endsWith('evaluation-capabilities')) return {schema:'advanced-rag-v2-evaluation',accepted:false,
        offline_only:true,max_branches:3,actions:['contains','where_used','bom_rollup','scope_comparison']} as never;
      if (path.endsWith('/graph-scopes')) return {items:[{product_scope:'assembly-A',site:'HCM'}],offset:0,has_more:false} as never;
      return {documents:[{DocID:1,OriginalFileName:'Cụm A.pdf'}]} as never;
    });
    vi.mocked(api.sendChatMessage).mockImplementation(async (_payload,callbacks)=>{
      callbacks.onDelta('Kết quả theo từng ý');
      callbacks.onDone({advanced_rag:{query:{schema:'query-plan-v2',status:'partial',accepted:false,offline_only:true,
        branches:[{branch_id:'b1',question:'Cụm chứa gì?',status:'partial',data_complete:true,
          graph:{source_status:'complete'},sources:[]},
          {branch_id:'b2',question:'So sánh hai kỳ',status:'blocked',sources:[]}]}}});
    });
    const {wrapper,pinia}=mountWithPinia(ChatView);
    useAuthStore(pinia).user={...user,roles:['reviewer']};
    await flushPromises();
    await wrapper.get('input[type="checkbox"]').setValue(true);await flushPromises();
    await wrapper.get('select[aria-label="Bản vẽ ý 1"]').setValue('1');
    await flushPromises();await wrapper.get('select[aria-label="Phạm vi sản phẩm ý 1"]').setValue('assembly-A');
    await wrapper.get('textarea').setValue('Tra cây cụm A');await button(wrapper,'Gửi').trigger('click');
    await flushPromises();
    expect(api.sendChatMessage).toHaveBeenCalledWith(expect.objectContaining({query_plan:{branches:[{
      branch_id:'b1',question:'Cụm chứa gì?',depends_on:[],graph_query:{root_key:'document:1',
        product_scope:'assembly-A',site:'HCM',direction:'contains'}}]}}),expect.anything());
    expect(wrapper.get('.branch-results').text()).toContain('Một phần');
    expect(wrapper.get('.branch-results').text()).toContain('Chưa đủ dữ liệu');
    expect(wrapper.get('.branch-results').text()).toContain('Cây nguồn: Đầy đủ');
  });
  it('renders separate sheet citations and opens their reviewed source rows', async () => {
    vi.mocked(api.apiGet).mockResolvedValue({items:[],snapshot_id:'s1',scope_hash:'b'.repeat(64),offset:0,has_more:false});
    vi.mocked(api.sendChatMessage).mockImplementation(async (_payload,callbacks) => {
      callbacks.onDelta('So sánh đã có căn cứ');
      callbacks.onDone({citations:['B2','B3'].map((cell,index)=>({
        doc_id:17,page_no:null,file_name:'ledger.xlsx',version_no:3,has_vision:false,
        original_url:'/api/files/documents/17/original', source_id:`D17L${index}`,
        citation_schema:'source-locator-v2',scope_id:index?'b':'a',snapshot_id:'s1',scope_hash:'b'.repeat(64),
        locator:{doc_id:'17',version:'3',sheet:'Sổ',cell,row_key:`row-${index+2}`},
      }))});
    });
    const {wrapper} = mountWithPinia(ChatView);
    await flushPromises();
    await wrapper.find('textarea').setValue('So sánh hai kỳ');
    await button(wrapper,'Gửi').trigger('click');
    await flushPromises();
    expect(wrapper.findAll('.citation-card')).toHaveLength(2);
    expect(wrapper.text()).toContain('Sheet Sổ · Ô B2');
    expect(wrapper.text()).toContain('Sheet Sổ · Ô B3');
    expect(wrapper.text()).not.toContain('trang null');
    await button(wrapper,'Xem số liệu nguồn').trigger('click');
    await flushPromises();
    expect(wrapper.find('table').exists()).toBe(true);
  });

  it('hides the old calculation when its source basis changes after the answer was loaded', async () => {
    vi.mocked(api.apiGet).mockResolvedValue({items:[],snapshot_id:'s1',scope_hash:'c'.repeat(64),offset:0,has_more:false});
    vi.mocked(api.sendChatMessage).mockImplementation(async (_payload,callbacks) => {
      callbacks.onDelta('Tổng A: 4,00 VND');
      callbacks.onDone({citations:[{doc_id:17,page_no:null,has_vision:false,original_url:'/api/files/documents/17/original',
        source_id:'D17L0123456789ABCDEF',citation_schema:'source-locator-v2',scope_id:'a',snapshot_id:'s1',scope_hash:'b'.repeat(64),
        locator:{doc_id:'17',version:'3',sheet:'Sổ',cell:'B2',row_key:'r2'}}]});
    });
    const {wrapper} = mountWithPinia(ChatView);
    await flushPromises();
    await wrapper.find('textarea').setValue('So sánh');
    await button(wrapper,'Gửi').trigger('click');
    await flushPromises();
    await button(wrapper,'Xem số liệu nguồn').trigger('click');
    await flushPromises();
    expect(wrapper.text()).not.toContain('Tổng A: 4,00 VND');
    expect(wrapper.findAll('.citation-card')).toHaveLength(0);
    expect(wrapper.text()).toContain('Nguồn đã thay đổi');
  });

  it('clears old evidence while history loads and handles server failures without leaking details',async()=>{
    vi.mocked(api.listSessions).mockResolvedValue([{session_id:'a',cau_hoi:'A'},{session_id:'b',cau_hoi:'B'}]);
    vi.mocked(api.loadHistory).mockResolvedValueOnce([{role:'assistant',content:'Old confidential answer',advanced_rag:{text_query:{
      schema:'query-text-v2',accepted:false,offline_only:true,branches:[{branch_id:'b1',subquery:'Old evidence',outcome:'full_answer',cited_sources:[]}]}}}]);
    let reject:(error:Error)=>void=()=>{};
    vi.mocked(api.loadHistory).mockImplementationOnce(()=>new Promise((_resolve,fail)=>{reject=fail;}));
    const {wrapper}=mountWithPinia(ChatView);await flushPromises();
    await button(wrapper,'Lịch sử').trigger('click');await wrapper.findAll('.history-title')[0].trigger('click');await flushPromises();
    expect(wrapper.text()).toContain('Old confidential answer');
    await button(wrapper,'Lịch sử').trigger('click');await wrapper.findAll('.history-title')[1].trigger('click');
    expect(wrapper.text()).not.toContain('Old confidential answer');expect(wrapper.find('.branch-results').exists()).toBe(false);
    await wrapper.get('textarea').setValue('New question');expect(button(wrapper,'Gửi').attributes('disabled')).toBeDefined();
    reject(new Error('HTTP 503 private database detail'));await flushPromises();
    expect(wrapper.text()).toContain('Không tải được lịch sử');expect(wrapper.text()).not.toContain('private database detail');
    expect(wrapper.text()).not.toContain('Đang tải lịch sử');
  });

  it('ignores a late history response after another session or a new chat is selected',async()=>{
    vi.mocked(api.listSessions).mockResolvedValue([{session_id:'a',cau_hoi:'A'},{session_id:'b',cau_hoi:'B'}]);
    let finish:(value:ChatMessage[])=>void=()=>{};
    vi.mocked(api.loadHistory).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}))
      .mockResolvedValueOnce([{role:'assistant',content:'Current B'}]);
    const {wrapper}=mountWithPinia(ChatView);await flushPromises();
    await button(wrapper,'Lịch sử').trigger('click');await wrapper.findAll('.history-title')[0].trigger('click');
    await wrapper.findAll('.history-title')[1].trigger('click');await flushPromises();
    finish([{role:'assistant',content:'Late A'}]);await flushPromises();
    expect(wrapper.text()).toContain('Current B');expect(wrapper.text()).not.toContain('Late A');
    vi.mocked(api.loadHistory).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    await button(wrapper,'Lịch sử').trigger('click');await wrapper.findAll('.history-title')[0].trigger('click');
    await button(wrapper,'Cuộc trò chuyện mới').trigger('click');
    finish([{role:'assistant',content:'Late after reset'}]);await flushPromises();
    expect(wrapper.text()).not.toContain('Late after reset');
  });

  it('discards history and session names when user permissions change or the view unmounts',async()=>{
    vi.mocked(api.listSessions).mockResolvedValue([{session_id:'a',cau_hoi:'Old session'}]);
    let finish:(value:ChatMessage[])=>void=()=>{};
    vi.mocked(api.loadHistory).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    const {wrapper,pinia}=mountWithPinia(ChatView);await flushPromises();
    const chat=useChatStore(pinia);chat.updateMemory(chat.sessionId,{currentPartIds:['private-part'],conversationContext:{private:true}});
    await button(wrapper,'Lịch sử').trigger('click');await wrapper.find('.history-title').trigger('click');
    vi.mocked(api.listSessions).mockResolvedValueOnce([]);
    useAuthStore(pinia).user={...user,user_id:8,username:'another'};await flushPromises();
    finish([{role:'assistant',content:'Late private answer'}]);await flushPromises();
    expect(wrapper.text()).not.toContain('Late private answer');expect(wrapper.text()).not.toContain('Old session');
    expect(chat.memoryBySession).toEqual({});
    let catalog:(value:{session_id:string;cau_hoi:string}[])=>void=()=>{};
    vi.mocked(api.listSessions).mockImplementationOnce(()=>new Promise(resolve=>{catalog=resolve;}));
    useAuthStore(pinia).user={...user,allowed_sites:['Other']};await flushPromises();
    wrapper.unmount();catalog([{session_id:'late',cau_hoi:'Late catalog'}]);await flushPromises();
  });

  it('ignores old stream callbacks after identity changes and reports catalog failures safely',async()=>{
    let callbacks:Parameters<typeof api.sendChatMessage>[1]|undefined;
    let finish:()=>void=()=>{};
    vi.mocked(api.sendChatMessage).mockImplementation((_payload,handlers)=>{callbacks=handlers;return new Promise(resolve=>{finish=resolve;});});
    const {wrapper,pinia}=mountWithPinia(ChatView);await flushPromises();
    await wrapper.get('textarea').setValue('Old question');await button(wrapper,'Gửi').trigger('click');await flushPromises();
    vi.mocked(api.listSessions).mockRejectedValueOnce(new Error('private catalog detail'));
    useAuthStore(pinia).user={...user,user_id:8};await flushPromises();
    callbacks!.onThinking();callbacks!.onDelta('Old stream secret');callbacks!.onWarning('Old warning');callbacks!.onError('Old error');
    callbacks!.onDone({new_part_ids:['old-private-part'],conversation_context:{old:true}});
    finish();await flushPromises();
    expect(wrapper.text()).not.toContain('Old stream secret');expect(wrapper.text()).not.toContain('Old warning');
    expect(wrapper.text()).not.toContain('Old error');expect(wrapper.text()).toContain('Không tải được danh sách lịch sử');
    expect(wrapper.text()).not.toContain('private catalog detail');expect(useChatStore(pinia).memoryBySession).toEqual({});
  });

  it('discards a late image upload after identity changes and reports image errors safely',async()=>{
    vi.stubGlobal('URL',{createObjectURL:vi.fn().mockReturnValue('blob:private'),revokeObjectURL:vi.fn()});
    let finish:(value:{image_id:string;image_token:string;file_name:string})=>void=()=>{};
    vi.mocked(api.uploadChatImage).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    const {wrapper,pinia}=mountWithPinia(ChatView);await flushPromises();
    const file=new File(['image'],'private.png',{type:'image/png'});
    const upload=wrapper.get('input[type="file"]');
    Object.defineProperty(upload.element,'files',{value:[file],configurable:true});await upload.trigger('change');
    useAuthStore(pinia).user={...user,user_id:8};await flushPromises();
    finish({image_id:'old',image_token:'private-token',file_name:'private.png'});await flushPromises();
    expect(wrapper.find('.selected-file').exists()).toBe(false);
    vi.mocked(api.sendChatMessage).mockResolvedValueOnce(undefined);
    await wrapper.get('textarea').setValue('Current question');await button(wrapper,'Gửi').trigger('click');await flushPromises();
    expect(vi.mocked(api.sendChatMessage).mock.calls.at(-1)![0].image_token).toBeNull();
    vi.mocked(api.uploadChatImage).mockRejectedValueOnce(new Error('private upload detail'));
    await upload.trigger('change');await flushPromises();
    expect(wrapper.text()).toContain('Không tải được ảnh');expect(wrapper.text()).not.toContain('private upload detail');
  });

  it('binds memory ownership on remount after identity changed outside ChatView',async()=>{
    const pinia=newPinia();useAuthStore(pinia).user=user;
    const first=mount(ChatView,{global:{...global,plugins:[pinia]}});await flushPromises();
    const chat=useChatStore(pinia), oldSession=chat.sessionId;
    chat.updateMemory(oldSession,{currentPartIds:['private-part'],conversationContext:{private:true}});
    first.unmount();useAuthStore(pinia).user={...user,user_id:8,username:'another'};
    const current=mount(ChatView,{global:{...global,plugins:[pinia]}});await flushPromises();
    expect(chat.sessionId).not.toBe(oldSession);expect(chat.memoryBySession).toEqual({});
    vi.mocked(api.sendChatMessage).mockResolvedValueOnce(undefined);
    await current.get('textarea').setValue('Current question');await button(current,'Gửi').trigger('click');await flushPromises();
    expect(vi.mocked(api.sendChatMessage).mock.calls.at(-1)![0]).toMatchObject({current_part_ids:[],conversation_context:null});
  });

  it('blocks send until the selected image finishes uploading and offers catalog retry',async()=>{
    vi.stubGlobal('URL',{createObjectURL:vi.fn().mockReturnValue('blob:image'),revokeObjectURL:vi.fn()});
    vi.mocked(api.listSessions).mockRejectedValueOnce(new Error('HTTP 503'));
    let finish:(value:{image_id:string;image_token:string;file_name:string})=>void=()=>{};
    vi.mocked(api.uploadChatImage).mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    const {wrapper}=mountWithPinia(ChatView);await flushPromises();
    vi.mocked(api.listSessions).mockResolvedValueOnce([]);
    await button(wrapper,'Thử lại danh sách lịch sử').trigger('click');await flushPromises();
    expect(wrapper.text()).not.toContain('Không tải được danh sách lịch sử');
    const upload=wrapper.get('input[type="file"]');
    Object.defineProperty(upload.element,'files',{value:[new File(['image'],'image.png',{type:'image/png'})],configurable:true});
    await upload.trigger('change');await wrapper.get('textarea').setValue('Image question');
    expect(button(wrapper,'Gửi').attributes('disabled')).toBeDefined();
    await wrapper.get('textarea').trigger('keydown',{key:'Enter'});expect(api.sendChatMessage).not.toHaveBeenCalled();
    finish({image_id:'ready',image_token:'ready-token',file_name:'image.png'});await flushPromises();
    vi.mocked(api.sendChatMessage).mockResolvedValueOnce(undefined);
    await button(wrapper,'Gửi').trigger('click');await flushPromises();
    expect(vi.mocked(api.sendChatMessage).mock.calls.at(-1)![0].image_token).toBe('ready-token');
  });

  it("opens an available OCR source image without claiming vision content", async () => {
    sessionStorage.setItem("chat-active-session", "session-ocr");
    vi.mocked(api.listSessions).mockResolvedValue([{ session_id: "session-ocr", cau_hoi: "OCR source question" }]);
    vi.mocked(api.loadHistory).mockResolvedValue([{
      role: "assistant",
      content: "OCR answer [SRC:D42P7]",
      citations: [{
        doc_id: 42, page_no: 7, file_name: "scan.pdf", version_no: 1,
        has_vision: false, page_url: "/api/files/documents/42/pages/7",
        original_url: "/api/files/documents/42/original",
      }],
    }]);
    const { wrapper } = mountWithPinia(ChatView);
    await flushPromises();

    expect(wrapper.text()).toContain("OCR answer");
    const preview = wrapper.find(".citation-preview-link");
    expect(preview.exists()).toBe(true);
    expect(preview.attributes("href")).toBe("/api/files/documents/42/pages/7");
    expect(preview.attributes("target")).toBe("_blank");
    expect(preview.find("img").attributes("src")).toBe(preview.attributes("href"));
    await preview.trigger("click");
    expect(wrapper.find(".citation-download").attributes("href"))
      .toBe("/api/files/documents/42/original");
    wrapper.unmount();
  });

  it("restores the active conversation after a page reload", async () => {
    vi.mocked(api.listSessions).mockResolvedValue([
      { session_id: "session-a", cau_hoi: "Câu hỏi A" },
    ]);
    vi.mocked(api.loadHistory).mockResolvedValue([
      { role: "assistant", content: "Câu trả lời đã lưu" },
    ]);
    const first = mountWithPinia(ChatView).wrapper;
    await flushPromises();
    await button(first, "Lịch sử").trigger("click");
    await first.find(".history-title").trigger("click");
    await flushPromises();
    first.unmount();

    const second = mountWithPinia(ChatView).wrapper;
    await flushPromises();
    expect(api.loadHistory).toHaveBeenCalledTimes(2);
    expect(useChatStore().sessionId).toBe("session-a");
    expect(second.text()).toContain("Câu trả lời đã lưu");

    await button(second, "Cuộc trò chuyện mới").trigger("click");
    expect(sessionStorage.getItem("chat-active-session")).toBeNull();
  });

  it("does not restore a session missing from the current user's history", async () => {
    sessionStorage.setItem("chat-active-session", "another-user-session");
    const { wrapper } = mountWithPinia(ChatView);
    await flushPromises();

    expect(api.loadHistory).not.toHaveBeenCalled();
    expect(wrapper.find(".empty-state").exists()).toBe(true);
    expect(sessionStorage.getItem("chat-active-session")).toBeNull();
  });

  it("loads, opens, and removes sessions while keeping the active session valid", async () => {
    const sessions = [
      { session_id: "session-a", cau_hoi: "Câu hỏi A" },
      { session_id: "session-b", cau_hoi: "Câu hỏi B" },
    ];
    vi.mocked(api.listSessions).mockResolvedValue(sessions);
    vi.mocked(api.loadHistory).mockResolvedValue([
      { role: "assistant", content: "Lịch sử A" },
    ]);
    const { wrapper } = mountWithPinia(ChatView);
    const chat = useChatStore();
    await flushPromises();

    await button(wrapper, "Lịch sử").trigger("click");
    await wrapper.findAll(".history-title")[0].trigger("click");
    await flushPromises();
    expect(api.loadHistory).toHaveBeenCalledWith("session-a");
    expect(chat.sessionId).toBe("session-a");
    expect(wrapper.text()).toContain("Lịch sử A");
    expect(wrapper.find(".history-panel").exists()).toBe(false);

    await button(wrapper, "Lịch sử").trigger("click");
    const secondRow = wrapper
      .findAll(".history-row")
      .find((row) => row.text().includes("Câu hỏi B"))!;
    await secondRow.find(".text-button").trigger("click");
    await flushPromises();
    expect(api.deleteSession).toHaveBeenCalledWith("session-b");
    expect(chat.sessionId).toBe("session-a");

    const firstRow = wrapper
      .findAll(".history-row")
      .find((row) => row.text().includes("Câu hỏi A"))!;
    await firstRow.find(".text-button").trigger("click");
    await flushPromises();
    expect(api.deleteSession).toHaveBeenCalledWith("session-a");
    expect(chat.sessionId).not.toBe("session-a");
    expect(api.listSessions).toHaveBeenCalledTimes(3);
  });

  it("uploads an image, sends a streamed answer, records memory, and accepts feedback", async () => {
    const createObjectURL = vi.fn().mockReturnValue("blob:pump");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    vi.mocked(api.uploadChatImage).mockResolvedValue({
      image_id: "image-7",
      image_token: "upload-token",
      file_name: "pump.png",
    });
    vi.mocked(api.sendChatMessage).mockImplementation(async (_payload, callbacks) => {
      callbacks.onThinking();
      callbacks.onDelta("Câu trả lời ");
      callbacks.onDelta("hoàn chỉnh");
      callbacks.onWarning("Nguồn tham khảo hạn chế");
      callbacks.onDone({
        chat_id: 42,
        ref_text: "Manual P-101",
        citations: [
          {
            doc_id: 10,
            page_no: 3,
            file_name: "manual.pdf",
            version_no: 2,
            has_vision: true,
            page_url: "/api/page/10/3",
            original_url: "/api/document/10",
          },
        ],
        new_part_ids: ["P-101"],
        conversation_context: { active: "manual" },
      });
    });
    const { wrapper } = mountWithPinia(ChatView);
    const chat = useChatStore();
    await flushPromises();
    const originalSession = chat.sessionId;
    const file = new File(["image"], "pump.png", { type: "image/png" });
    const fileInput = wrapper.find<HTMLInputElement>('input[type="file"]');
    Object.defineProperty(fileInput.element, "files", {
      configurable: true,
      value: [file],
    });

    await fileInput.trigger("change");
    await flushPromises();
    expect(api.uploadChatImage).toHaveBeenCalledWith(file);
    expect(wrapper.text()).toContain("pump.png");
    expect(createObjectURL).toHaveBeenCalledWith(file);

    await wrapper.find("textarea").setValue("  Phân tích P-101  ");
    await button(wrapper, "Gửi").trigger("click");
    await flushPromises();

    expect(api.sendChatMessage).toHaveBeenCalledWith(
      {
        session_id: originalSession,
        question: "Phân tích P-101",
        image_token: "upload-token",
        chat_history: [],
        current_part_ids: [],
        conversation_context: null,
      },
      expect.any(Object),
    );
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:pump");
    expect(wrapper.text()).toContain("Câu trả lời hoàn chỉnh");
    expect(wrapper.text()).toContain("Nguồn tham khảo hạn chế");
    expect(wrapper.text()).toContain("Manual P-101");
    expect(wrapper.text()).toContain("manual.pdf · trang 3");
    expect(wrapper.text()).toContain("version 2");
    expect(wrapper.find('a[href="/api/document/10"]').exists()).toBe(true);
    expect(chat.currentMemory).toEqual({
      chatId: 42,
      currentPartIds: ["P-101"],
      conversationContext: { active: "manual" },
    });
    expect(sessionStorage.getItem("chat-active-session")).toBe(originalSession);

    await button(wrapper, "Hữu ích").trigger("click");
    await flushPromises();
    expect(api.sendFeedback).toHaveBeenCalledWith(42, 1);
  });

  it("renders a legacy markdown source appendix as readable source lines", async () => {
    vi.mocked(api.listSessions).mockResolvedValue([{ session_id: "session-source", cau_hoi: "Nguồn?" }]);
    vi.mocked(api.loadHistory).mockResolvedValue([
      {
        role: "assistant",
        content: "Câu trả lời [SRC:D134P1]",
        ref_text: "---\n**Nguồn tham chiếu:**\n- **manual.pdf** (Trang 3)\n[SRC:D134P1]",
      },
    ]);
    const { wrapper } = mountWithPinia(ChatView);
    await flushPromises();

    await button(wrapper, "Lịch sử").trigger("click");
    await wrapper.find(".history-title").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Nguồn tài liệu D134, trang 1");
    expect(wrapper.text()).toContain("manual.pdf (Trang 3)");
    expect(wrapper.text()).not.toContain("[SRC:");
    expect(wrapper.text()).not.toContain("**Nguồn");
    expect(wrapper.text()).not.toContain("---");
  });

  it("uses a prompt suggestion and renders an SSE error as the assistant answer", async () => {
    vi.mocked(api.sendChatMessage).mockImplementation(async (_payload, callbacks) => {
      callbacks.onError("Không tìm thấy tài liệu");
    });
    const { wrapper } = mountWithPinia(ChatView);
    await flushPromises();

    await wrapper.findAll(".prompt-grid button")[0].trigger("click");
    expect((wrapper.find("textarea").element as HTMLTextAreaElement).value).toContain(
      "Production",
    );
    await button(wrapper, "Gửi").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Không tìm thấy tài liệu");
    expect(wrapper.text()).toContain("Lỗi: Không tìm thấy tài liệu");
    expect(wrapper.find(".progress-list li.error").exists()).toBe(true);
  });

  it.each([
    [new Error("Kết nối thất bại"), "Kết nối thất bại"],
    ["opaque", "Gửi câu hỏi thất bại"],
  ])("reports a rejected send without leaving the page busy", async (rejection, expected) => {
    vi.mocked(api.sendChatMessage).mockRejectedValue(rejection);
    const { wrapper } = mountWithPinia(ChatView);
    await flushPromises();

    await wrapper.find("textarea").setValue("P-101");
    await button(wrapper, "Gửi").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain(expected);
    expect(wrapper.find(".progress-list li.error").exists()).toBe(true);
    expect(wrapper.find(".tag").text()).toBe("Sẵn sàng");
    expect(wrapper.find(".thinking-row").exists()).toBe(false);
  });

  it("discards an interrupted answer and leaves the question ready to retry", async () => {
    vi.mocked(api.sendChatMessage).mockImplementation(async (_payload, callbacks) => {
      callbacks.onDelta("QA_PARTIAL_STREAM");
      throw new Error("Phản hồi chưa hoàn tất. Vui lòng thử lại.");
    });
    const { wrapper } = mountWithPinia(ChatView);
    await flushPromises();

    await wrapper.find("textarea").setValue("P-101?");
    await button(wrapper, "Gửi").trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Phản hồi chưa hoàn tất. Vui lòng thử lại.");
    expect(wrapper.text()).not.toContain("QA_PARTIAL_STREAM");
    expect((wrapper.find("textarea").element as HTMLTextAreaElement).value).toBe("P-101?");
    expect(wrapper.find(".progress-list li.error").exists()).toBe(true);
    expect(wrapper.find(".tag").text()).toBe("Sẵn sàng");

    vi.mocked(api.sendChatMessage).mockImplementationOnce(async (_payload, callbacks) => {
      callbacks.onDone({ chat_id: 43 });
    });
    await button(wrapper, "Gửi").trigger("click");
    await flushPromises();
    expect(api.sendChatMessage).toHaveBeenLastCalledWith(
      expect.objectContaining({ question: "P-101?", chat_history: [] }),
      expect.any(Object),
    );
  });

  it("ignores an empty file selection and can clear a selected upload", async () => {
    const createObjectURL = vi.fn().mockReturnValue("blob:selected");
    const revokeObjectURL = vi.fn();
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
    vi.mocked(api.uploadChatImage).mockResolvedValue({
      image_id: "image-8",
      image_token: "token-8",
      file_name: "selected.png",
    });
    const { wrapper } = mountWithPinia(ChatView);
    await flushPromises();
    const fileInput = wrapper.find<HTMLInputElement>('input[type="file"]');

    await fileInput.trigger("change");
    expect(api.uploadChatImage).not.toHaveBeenCalled();

    const file = new File(["image"], "selected.png", { type: "image/png" });
    Object.defineProperty(fileInput.element, "files", {
      configurable: true,
      value: [file],
    });
    await fileInput.trigger("change");
    await flushPromises();
    await button(wrapper, "Bỏ file").trigger("click");

    expect(revokeObjectURL).toHaveBeenCalledWith("blob:selected");
    expect(wrapper.find(".selected-file").exists()).toBe(false);
  });
});

describe("DashboardView public behavior", () => {
  it("does not claim recent documents are empty when the group is unavailable", async () => {
    vi.mocked(api.loadDashboard).mockResolvedValue({
      document_lifecycle: { effective: 0 },
    } as Awaited<ReturnType<typeof api.loadDashboard>>);
    const pinia = newPinia();
    useAuthStore().user = { ...user, roles: ["platform_admin"] };
    const wrapper = mount(DashboardView, {
      global: { ...global, plugins: [pinia] },
    });
    await flushPromises();

    expect(wrapper.text()).not.toContain("Tài liệu gần đây");
  });

  it.each(["platform_admin", "security_admin"])(
    "does not offer document or queue links without route access for %s",
    async (role) => {
      vi.mocked(api.loadDashboard).mockResolvedValue({
        stats: {},
        document_lifecycle: { effective: 3 },
        ingestion: { running: 2 },
        recent_documents: [],
        recent_failed_jobs: [],
      });
      const pinia = newPinia();
      useAuthStore().user = { ...user, roles: [role] };
      const wrapper = mount(DashboardView, {
        global: { ...global, plugins: [pinia] },
      });
      await flushPromises();

      const cards = wrapper.findAll(".metric-card");
      expect(cards.length).toBe(2);
      for (const card of cards) {
        expect(card.classes()).not.toContain("actionable");
        await card.trigger("click");
      }
      expect(routerState.push).not.toHaveBeenCalled();
    },
  );

  it("renders grouped metrics and recent activity, then opens actionable metrics", async () => {
    vi.mocked(api.loadDashboard).mockResolvedValue({
      stats: { ignored: 99 },
      ingestion: { running: 2 },
      review: { pending: 4 },
      recent_documents: [
        {
          TenFile: "",
          OriginalFileName: "manual.pdf",
          Department: null,
          ThuMuc: "Engineering",
          LifecycleStatus: "effective",
        },
        {},
      ],
      recent_failed_jobs: [
        { file: "broken.pdf", ErrorMessage: "", error: "OCR failed" },
      ],
    });
    const pinia = newPinia();
    useAuthStore().user = { ...user, roles: ["admin"] };
    const wrapper = mount(DashboardView, {
      global: { ...global, plugins: [pinia] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Công việc tài liệu và quản trị nội dung");
    expect(wrapper.text()).toContain("Ingest");
    expect(wrapper.text()).toContain("Job đang chạy");
    expect(wrapper.text()).toContain("Chờ duyệt");
    expect(wrapper.text()).not.toContain("ignored");
    expect(wrapper.text()).toContain("manual.pdf");
    expect(wrapper.text()).toContain("Engineering · effective");
    expect(wrapper.text()).toContain("broken.pdf");
    expect(wrapper.text()).toContain("OCR failed");
    expect(wrapper.text()).toContain("—");

    const runningCard = wrapper
      .findAll(".metric-card")
      .find((card) => card.text().includes("Job đang chạy"))!;
    await runningCard.trigger("click");
    expect(routerState.push).toHaveBeenCalledWith("/queue");
  });

  it("falls back to legacy stats and leaves non-actionable metrics in place", async () => {
    vi.mocked(api.loadDashboard).mockResolvedValue({
      stats: { departments_planned: 3 },
      recent_documents: [],
      recent_failed_jobs: [],
    });
    const pinia = newPinia();
    useAuthStore().user = { ...user, roles: ["viewer"] };
    const wrapper = mount(DashboardView, {
      global: { ...global, plugins: [pinia] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Tổng quan");
    expect(wrapper.text()).toContain("Phòng ban đã lên kế hoạch");
    expect(wrapper.text()).toContain("Không có dữ liệu.");
    expect(wrapper.text()).toContain("Không có job lỗi.");
    const metric = wrapper.find(".metric-card");
    expect(metric.classes()).not.toContain("actionable");
    await metric.trigger("click");
    expect(routerState.push).not.toHaveBeenCalled();
  });

  it.each([
    [["admin"], "Công việc tài liệu và quản trị nội dung"],
    [["reviewer"], "Công việc duyệt và quản trị tri thức"],
    [["uploader"], "Tiến độ tài liệu bạn phụ trách"],
    [[], "Tài liệu và hoạt động của bạn"],
  ])("shows the dashboard summary for roles %j", async (roles, summary) => {
    vi.mocked(api.loadDashboard).mockResolvedValue({
      stats: {},
      recent_documents: [],
      recent_failed_jobs: [],
    });
    const pinia = newPinia();
    useAuthStore().user = { ...user, roles };
    const wrapper = mount(DashboardView, {
      global: { ...global, plugins: [pinia] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(summary);
  });

  it.each([
    [new Error("Dashboard offline"), "Dashboard offline"],
    ["opaque", "Không tải được tổng quan"],
  ])("shows a load failure and clears the loading state", async (rejection, expected) => {
    let rejectLoad!: (reason: unknown) => void;
    vi.mocked(api.loadDashboard).mockImplementationOnce(
      () =>
        new Promise((_resolve, reject) => {
          rejectLoad = reject;
        }),
    );
    const { wrapper } = mountWithPinia(DashboardView);
    await nextTick();
    expect(wrapper.find(".spinner").exists()).toBe(true);

    rejectLoad(rejection);
    await flushPromises();

    expect(wrapper.text()).toContain(expected);
    expect(wrapper.find(".spinner").exists()).toBe(false);
  });
});

describe("LoginView public behavior", () => {
  it.each([
    [{ next: "/documents?tab=expired" }, "/documents?tab=expired"],
    [{}, "/dashboard"],
  ])("submits credentials and follows the expected destination", async (query, destination) => {
    routerState.query = query;
    vi.mocked(api.login).mockResolvedValue(user);
    const { wrapper } = mountWithPinia(LoginView);
    const auth = useAuthStore();
    const inputs = wrapper.findAll("input");

    await inputs[0].setValue("bao");
    await inputs[1].setValue("secret");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(api.login).toHaveBeenCalledWith("bao", "secret");
    expect(auth.user).toEqual(user);
    expect(auth.ready).toBe(true);
    expect(routerState.push).toHaveBeenCalledWith(destination);
  });

  it("renders authentication errors and the pending state from the auth store", async () => {
    const pinia = newPinia();
    const auth = useAuthStore();
    auth.error = "Sai tên đăng nhập hoặc mật khẩu";
    auth.loading = true;
    const wrapper = mount(LoginView, {
      global: { ...global, plugins: [pinia] },
    });

    expect(wrapper.text()).toContain("Sai tên đăng nhập hoặc mật khẩu");
    expect(button(wrapper, "Đăng nhập").attributes("data-loading")).toBe("true");
  });

  it("turns an upstream HTML login response into short retryable copy", async () => {
    const pinia = newPinia();
    const auth = useAuthStore();
    vi.mocked(api.login).mockRejectedValue(
      new Error("<!doctype html><html><body>ngrok 3004 upstream failure</body></html>"),
    );

    await expect(auth.login("bao", "secret")).rejects.toThrow();
    const wrapper = mount(LoginView, {
      global: { ...global, plugins: [pinia] },
    });

    expect(wrapper.text()).toContain("Dịch vụ đăng nhập đang tạm thời không khả dụng. Vui lòng thử lại.");
    expect(wrapper.text()).not.toContain("ngrok 3004");
    expect(wrapper.text()).not.toContain("<html>");
  });
});
