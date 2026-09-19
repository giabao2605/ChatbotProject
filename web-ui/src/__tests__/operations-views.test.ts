import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { defineComponent, nextTick, type Component } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiRow, CreateForm, RowAction, ToolbarAction } from "@/types";
import * as api from "@/api/client";
import AccessView from "@/views/AccessView.vue";
import AnalyticsView from "@/views/AnalyticsView.vue";
import AuditView from "@/views/AuditView.vue";
import DictionaryView from "@/views/DictionaryView.vue";
import DocumentsView from "@/views/DocumentsView.vue";
import FeedbackView from "@/views/FeedbackView.vue";
import GlossaryView from "@/views/GlossaryView.vue";
import HelpView from "@/views/HelpView.vue";
import LifecycleView from "@/views/LifecycleView.vue";
import MaterialsView from "@/views/MaterialsView.vue";
import ObservabilityView from "@/views/ObservabilityView.vue";
import QualityView from "@/views/QualityView.vue";
import QueueView from "@/views/QueueView.vue";
import RegressionView from "@/views/RegressionView.vue";

const state = vi.hoisted(() => ({
  replace: vi.fn(),
  route: { query: {} as Record<string, unknown> },
  roles: ["admin", "reviewer", "security_admin"],
}));

vi.mock("vue-router", () => ({
  useRoute: () => state.route,
  useRouter: () => ({ replace: state.replace }),
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: () => ({ user: { roles: state.roles } }),
}));

vi.mock("@/api/client", () => ({
  apiGet: vi.fn(),
  apiSend: vi.fn(),
}));

const ResourcePageStub = defineComponent({
  name: "ResourcePage",
  props: {
    title: String,
    eyebrow: String,
    description: String,
    load: Function,
    columns: Array,
    rowActions: Array,
    toolbar: Array,
    createForm: Object,
    filters: Array,
  },
  setup(_, { expose }) {
    expose({ refresh: vi.fn() });
  },
  template: '<div class="resource-page-stub" :data-title="title"></div>',
});

const StatViewStub = defineComponent({
  name: "StatView",
  props: {
    title: String,
    eyebrow: String,
    description: String,
    load: Function,
    toolbar: Array,
  },
  template: '<div class="stat-view-stub" :data-title="title"></div>',
});

const ButtonStub = defineComponent({
  name: "Button",
  inheritAttrs: false,
  props: { label: String, type: String },
  emits: ["click"],
  template: '<button :type="type || \'button\'" @click="$emit(\'click\')">{{ label }}</button>',
});

const DialogStub = defineComponent({
  name: "Dialog",
  props: { visible: Boolean, header: String },
  emits: ["update:visible", "hide"],
  template: '<div v-if="visible" class="dialog-stub"><slot /><slot name="footer" /></div>',
});

const InputTextStub = defineComponent({
  name: "InputText",
  props: { modelValue: [String, Number] },
  emits: ["update:modelValue"],
  template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
});

const LifecycleViewStub = defineComponent({
  name: "LifecycleView",
  props: { group: String },
  template: '<div class="lifecycle-view-stub"></div>',
});

const MaterialsViewStub = defineComponent({
  name: "MaterialsView",
  template: '<div class="materials-view-stub"></div>',
});

const GlossaryViewStub = defineComponent({
  name: "GlossaryView",
  template: '<div class="glossary-view-stub"></div>',
});

const stubs = {
  ResourcePage: ResourcePageStub,
  StatView: StatViewStub,
  Button: ButtonStub,
  Dialog: DialogStub,
  InputText: InputTextStub,
  Message: { template: "<div><slot /></div>" },
  Card: { template: '<div><slot name="title" /><slot name="content" /></div>' },
  LifecycleView: LifecycleViewStub,
  MaterialsView: MaterialsViewStub,
  GlossaryView: GlossaryViewStub,
};

type PageContract = {
  load: (filters?: Record<string, unknown>) => Promise<unknown>;
  rowActions?: RowAction[];
  toolbar?: ToolbarAction[];
  createForm?: CreateForm;
};

function mountView(component: Component, props: Record<string, unknown> = {}): VueWrapper {
  return mount(component as never, { props: props as never, global: { stubs } });
}

function pages(wrapper: VueWrapper): PageContract[] {
  return wrapper
    .findAllComponents(ResourcePageStub)
    .map((item) => item.props() as unknown as PageContract);
}

function button(wrapper: VueWrapper, label: string, selector = "button") {
  const match = wrapper.findAll(selector).find((item) => item.text().includes(label));
  if (!match) throw new Error(`Missing button: ${label}`);
  return match;
}

function readKey(path: string, params?: Record<string, unknown>) {
  return `${path} ${JSON.stringify(params ?? null)}`;
}

function responseFor(path: string): unknown {
  const responses: Record<string, unknown> = {
    "/api/catalog/departments": {
      departments: [{ code: "ME", name: "Cơ khí" }, { DeptCode: "QA", DeptName: "Chất lượng" }],
    },
    "/api/access/my-requests": { requests: [{ request_id: 1 }] },
    "/api/access/requests": { requests: [{ RequestID: 2 }] },
    "/api/access/grants": { grants: [{ action: "grant" }] },
    "/api/access/users": {
      users: [{ user_id: 8, username: "bao", max_level: "internal", departments: ["ME", "QA"] }],
    },
    "/api/analytics/usage": { requests: 12 },
    "/api/analytics/departments": { ME: 8 },
    "/api/analytics/cache": { hit_rate: 0.8 },
    "/api/analytics/observability": { p95_ms: 900 },
    "/api/audit": { logs: [{ AuditID: 1 }] },
    "/api/documents": { documents: [{ DocID: 7 }] },
    "/api/feedback": { feedbacks: [{ FeedbackID: 3 }] },
    "/api/glossary": { terms: [{ glossary_id: 4 }] },
    "/api/lifecycle": {
      expired: [{ doc_id: 9 }],
      expiring_soon: [{ doc_id: 10 }],
      needs_review: [{ doc_id: 11 }],
    },
    "/api/materials": {
      materials: [{
        material_id: 5,
        code: "M5",
        display: "Thép",
        synonyms: [{ synonym_id: 51, synonym: "Steel", is_active: true }],
      }],
    },
    "/api/quality/documents": { documents: [{ doc_id: 12 }] },
    "/api/ingestion/jobs": { jobs: [{ JobID: 13 }] },
    "/api/ingestion/eta": {
      eta_seconds: { pending: 2, avg_seconds: 4, eta_seconds: 8 },
    },
    "/api/regression/questions": { questions: [{ RegQID: 14 }] },
    "/api/regression/runs": { runs: [{ batch_id: "old" }] },
  };
  if (!Object.hasOwn(responses, path)) throw new Error(`Unexpected GET path: ${path}`);
  return responses[path];
}

const allowedReads = new Set([
  readKey("/api/catalog/departments", { active_only: true }),
  readKey("/api/access/my-requests"),
  readKey("/api/access/requests", { status_value: "pending" }),
  readKey("/api/access/requests", { status_value: "all", limit: 200 }),
  readKey("/api/access/grants", { limit: 100 }),
  readKey("/api/access/users"),
  readKey("/api/analytics/usage", { days: 30 }),
  readKey("/api/analytics/departments", { days: 30 }),
  readKey("/api/analytics/cache"),
  readKey("/api/analytics/observability", { days: 7 }),
  readKey("/api/audit", { limit: 200 }),
  readKey("/api/documents", { search: "pump", bucket: "effective", soon_days: 30 }),
  readKey("/api/feedback", { only_pending: true }),
  readKey("/api/glossary", { active_only: false }),
  readKey("/api/lifecycle", { soon_days: 30 }),
  readKey("/api/materials"),
  readKey("/api/quality/documents", { limit: 100, worst_first: true }),
  readKey("/api/ingestion/jobs", { status_value: "failed" }),
  readKey("/api/ingestion/eta"),
  readKey("/api/regression/questions", { active_only: false }),
  readKey("/api/regression/runs"),
]);

const allowedWrites = new Set([
  "POST /api/access/request",
  "POST /api/access/requests/22/resolve",
  "POST /api/access/users/8/revoke-clearance",
  "POST /api/access/users/8/revoke-department",
  "POST /api/quality/recompute",
  "POST /api/quality/cleanup",
  "PATCH /api/documents/7/current",
  "PATCH /api/documents/7/expired",
  "DELETE /api/documents/7",
  "PATCH /api/documents/7/metadata",
  "DELETE /api/documents/8",
  "POST /api/glossary",
  "PATCH /api/glossary/4/active",
  "DELETE /api/glossary/4",
  "POST /api/materials",
  "DELETE /api/materials/5",
  "POST /api/materials/5/synonyms",
  "DELETE /api/materials/synonyms/51",
  "DELETE /api/feedback/3",
  "POST /api/feedback/3/classify",
  "POST /api/lifecycle/refresh-expired",
  "POST /api/lifecycle/documents/9/reviewed",
  "PATCH /api/lifecycle/documents/9",
  "POST /api/ingestion/jobs/13/cancel",
  "POST /api/ingestion/jobs/13/requeue",
  "PATCH /api/ingestion/jobs/13/priority",
  "POST /api/ingestion/jobs/13/pending-review",
  "DELETE /api/ingestion/jobs/13",
  "POST /api/ingestion/jobs/bulk-delete",
  "POST /api/regression/questions",
  "PATCH /api/regression/questions/14/active",
  "POST /api/regression/run",
]);

beforeEach(() => {
  vi.clearAllMocks();
  state.route.query = {};
  state.roles = ["admin", "reviewer", "security_admin"];
  vi.mocked(api.apiGet).mockImplementation(
    (path, params) => {
      const key = readKey(path, params);
      if (!allowedReads.has(key)) {
        return Promise.reject(new Error(`Unexpected read: ${key}`)) as never;
      }
      return Promise.resolve(responseFor(path)) as never;
    },
  );
  vi.mocked(api.apiSend).mockImplementation(
    (path, method) => {
      if (!allowedWrites.has(`${method} ${path}`)) {
        return Promise.reject(new Error(`Unexpected write: ${method} ${path}`)) as never;
      }
      return Promise.resolve(path === "/api/regression/run"
        ? { ok: true, summary: { batch_id: "b-1", total: 5, passed: 4, failed: 1, pass_rate: 0.8 } }
        : { ok: true }) as never;
    },
  );
});

describe("thin operations views", () => {
  it("runs analytics, observability, audit, and quality contracts", async () => {
    state.roles = [...state.roles, "platform_admin"];
    const analytics = mountView(AnalyticsView);
    for (const stat of analytics.findAllComponents(StatViewStub)) {
      await (stat.props("load") as () => Promise<unknown>)();
    }
    const observability = mountView(ObservabilityView);
    await (observability.findComponent(StatViewStub).props("load") as () => Promise<unknown>)();

    const audit = mountView(AuditView);
    expect(await pages(audit)[0].load()).toEqual([{ AuditID: 1 }]);

    const quality = mountView(QualityView);
    const qualityPage = pages(quality)[0];
    expect(await qualityPage.load?.({ worst_first: true })).toEqual([{ doc_id: 12 }]);
    for (const action of qualityPage.toolbar ?? []) await action.run();

    expect(vi.mocked(api.apiGet)).toHaveBeenCalledWith("/api/analytics/usage", { days: 30 });
    expect(vi.mocked(api.apiGet)).toHaveBeenCalledWith("/api/analytics/departments", { days: 30 });
    expect(vi.mocked(api.apiGet)).toHaveBeenCalledWith("/api/analytics/cache");
    expect(vi.mocked(api.apiGet)).toHaveBeenCalledWith("/api/analytics/observability", { days: 7 });
    expect(vi.mocked(api.apiSend)).not.toHaveBeenCalledWith("/api/quality/cleanup", "POST");
  });

  it("switches dictionary tabs and renders help content", async () => {
    const dictionary = mountView(DictionaryView);
    expect(dictionary.find(".materials-view-stub").exists()).toBe(true);
    await button(dictionary, "Thuật ngữ").trigger("click");
    expect(state.replace).toHaveBeenCalledWith({ path: "/dictionary", query: { tab: "glossary" } });

    state.route.query = { tab: "glossary" };
    const glossaryTab = mountView(DictionaryView);
    expect(glossaryTab.find(".glossary-view-stub").exists()).toBe(true);
    const help = mountView(HelpView);
    expect(help.text()).toContain("Chat & trích dẫn");
  });
});

describe("access and document operations", () => {
  it("loads and executes access request and revocation contracts", async () => {
    const wrapper = mountView(AccessView);
    await flushPromises();
    const contracts = pages(wrapper);
    expect(contracts).toHaveLength(5);
    for (const contract of contracts) await contract.load?.({});
    expect(await contracts[0].createForm?.submit({ request_type: "department" })).toEqual({ ok: true });
    for (const action of contracts[1].rowActions ?? []) {
      await action.run({ RequestID: 22 });
    }
    expect(vi.mocked(api.apiGet)).toHaveBeenCalledWith(
      "/api/catalog/departments",
      { active_only: true },
    );
    expect(vi.mocked(api.apiGet)).toHaveBeenCalledWith(
      "/api/access/requests",
      { status_value: "pending" },
    );
    expect(vi.mocked(api.apiGet)).toHaveBeenCalledWith(
      "/api/access/requests",
      { status_value: "all", limit: 200 },
    );
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith(
      "/api/access/requests/22/resolve",
      "POST",
      { decision: "approved" },
    );
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith(
      "/api/access/requests/22/resolve",
      "POST",
      { decision: "rejected" },
    );

    const user = { user_id: 8, username: "bao", max_level: "internal", departments: ["ME"] };
    const clearance = contracts[4].rowActions?.[0].run(user);
    await nextTick();
    await wrapper.find(".dialog-stub form").trigger("submit");
    await clearance;

    const department = contracts[4].rowActions?.[1].run(user);
    await nextTick();
    await wrapper.find(".dialog-stub form").trigger("submit");
    await department;

    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith(
      "/api/access/users/8/revoke-department",
      "POST",
      { department: "ME", reason: "" },
    );
  });

  it("runs document loads, visibility rules, metadata edit, bulk delete, and lifecycle tab", async () => {
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    const wrapper = mountView(DocumentsView);
    const contract = pages(wrapper)[0];
    expect(await contract.load?.({ search: "pump" })).toEqual([{ DocID: 7 }]);
    const actions = contract.rowActions ?? [];
    await actions[0].run({ DocID: 7 });
    await actions[1].run({ DocID: 7 });
    expect(open).toHaveBeenCalledTimes(2);
    expect(actions[3].visible?.({ IsCurrentVersion: 0 })).toBe(true);
    expect(actions[3].visible?.({ IsCurrentVersion: 1 })).toBe(false);
    for (const action of actions.slice(3)) await action.run({ DocID: 7 });

    const edit = actions[2].run({
      DocID: 7,
      title: "Bơm",
      issued_date: "2026-07-31T00:00:00",
    });
    await nextTick();
    await wrapper.find(".dialog-stub form").trigger("submit");
    await edit;

    const bulk = contract.toolbar?.[0].run();
    await nextTick();
    await wrapper.find(".dialog-stub textarea").setValue("7, 8 invalid");
    await button(wrapper, "Xóa", ".dialog-stub button").trigger("click");
    await flushPromises();
    await bulk;
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith("/api/documents/8", "DELETE");

    state.route.query = { tab: "expiring-soon" };
    const lifecycle = mountView(DocumentsView);
    expect(lifecycle.findComponent(LifecycleViewStub).props("group")).toBe("expiring_soon");
  });
});

describe("dictionary and quality resources", () => {
  it("normalizes and mutates glossary entries", async () => {
    const wrapper = mountView(GlossaryView);
    const contract = pages(wrapper)[0];
    expect(await contract.load?.()).toEqual([{ glossary_id: 4 }]);
    await contract.createForm?.submit({ term: "BOM", domain: "ME", synonyms: "bill, list, " });
    await contract.createForm?.submit({ term: "CAD", domain: "ME", synonyms: ["drawing"] });
    for (const action of contract.rowActions ?? []) await action.run({ glossary_id: 4 });
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith(
      "/api/glossary",
      "POST",
      expect.objectContaining({ synonyms: ["bill", "list"] }),
    );
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith(
      "/api/glossary/4/active",
      "PATCH",
      { is_active: true },
    );
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith(
      "/api/glossary/4/active",
      "PATCH",
      { is_active: false },
    );
  });

  it("loads materials and executes create, synonym, and delete flows", async () => {
    const wrapper = mountView(MaterialsView);
    const contract = pages(wrapper)[0];
    expect(await contract.load?.()).toEqual([
      expect.objectContaining({ material_id: 5, synonyms_text: "Steel" }),
    ]);
    await contract.createForm?.submit({ code: "M6", display: "Nhôm" });
    await contract.rowActions?.[1].run({ material_id: 5 });

    const synonym = contract.rowActions?.[0].run({
      material_id: 5,
      display: "Thép",
      synonyms: [{ synonym_id: 51, synonym: "Steel", is_active: true }],
    });
    await nextTick();
    await wrapper.find('.dialog-stub input').setValue("metal");
    await wrapper.find(".dialog-stub form").trigger("submit");
    await flushPromises();
    await button(wrapper, "Xoá", ".dialog-stub button").trigger("click");
    await flushPromises();
    await button(wrapper, "Đóng", ".dialog-stub button").trigger("click");
    await synonym;

    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith(
      "/api/materials/5/synonyms",
      "POST",
      { synonym: "metal" },
    );
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith("/api/materials/synonyms/51", "DELETE");
  });

  it("classifies and deletes feedback", async () => {
    const wrapper = mountView(FeedbackView);
    const contract = pages(wrapper)[0];
    expect(await contract.load?.({ only_pending: true })).toEqual([{ FeedbackID: 3 }]);
    await contract.rowActions?.[1].run({ FeedbackID: 3 });

    const classify = contract.rowActions?.[0].run({ FeedbackID: 3, Question: "Sai ở đâu?" });
    await nextTick();
    await wrapper.find(".dialog-stub input").setValue("wrong_answer");
    const notes = wrapper.findAll(".dialog-stub textarea");
    await notes[0].setValue("Đáp án đúng");
    await notes[1].setValue("Đã kiểm tra");
    await button(wrapper, "Lưu", ".dialog-stub button").trigger("click");
    await flushPromises();
    await classify;
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith(
      "/api/feedback/3/classify",
      "POST",
      {
        failure_type: "wrong_answer",
        correct_answer: "Đáp án đúng",
        reviewer_note: "Đã kiểm tra",
      },
    );
  });
});

describe("lifecycle, queue, and regression operations", () => {
  it("loads lifecycle groups and submits review and date changes", async () => {
    const wrapper = mountView(LifecycleView, { group: "expired" });
    const contract = pages(wrapper)[0];
    expect(await contract.load?.()).toEqual([{ doc_id: 9 }]);
    await contract.toolbar?.[0].run();

    const reviewed = contract.rowActions?.[0].run({ doc_id: 9, file: "pump.pdf" });
    await nextTick();
    await wrapper.find('.dialog-stub input[type="number"]').setValue("90");
    await button(wrapper, "Lưu", ".dialog-stub button").trigger("click");
    await flushPromises();
    await reviewed;

    const dates = contract.rowActions?.[1].run({
      doc_id: 9,
      file: "pump.pdf",
      effective_date: "2026-01-01T00:00:00",
      expiry_date: "invalid",
    });
    await nextTick();
    await wrapper.find('.dialog-stub input[type="date"]').setValue("2026-01-01");
    await button(wrapper, "Lưu", ".dialog-stub button").trigger("click");
    await flushPromises();
    await dates;
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith(
      "/api/lifecycle/documents/9",
      "PATCH",
      { effective_date: "2026-01-01" },
    );
  });

  it("loads queue metrics and executes row and bulk actions", async () => {
    const wrapper = mountView(QueueView);
    await flushPromises();
    expect(wrapper.findComponent(ResourcePageStub).props("description")).toContain("2 job chờ");
    const contract = pages(wrapper)[0];
    expect(await contract.load?.({ status_value: "failed" })).toEqual([{ JobID: 13 }]);
    for (const action of contract.rowActions ?? []) await action.run({ JobID: 13 });

    const bulk = contract.toolbar?.[0].run();
    await nextTick();
    await wrapper.find(".dialog-stub textarea").setValue("13 14 bad");
    await button(wrapper, "Xoá", ".dialog-stub button").trigger("click");
    await flushPromises();
    await bulk;
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith(
      "/api/ingestion/jobs/bulk-delete",
      "POST",
      { ids: [13, 14] },
    );
  });

  it("loads, edits, and runs the regression suite", async () => {
    const wrapper = mountView(RegressionView);
    const contracts = pages(wrapper);
    expect(await contracts[0].load?.()).toEqual([{ RegQID: 14 }]);
    expect(await contracts[1].load?.()).toEqual([{ batch_id: "old" }]);
    await contracts[0].createForm?.submit({ question: "Q?", expected_doc_id: "12" });
    for (const action of contracts[0].rowActions ?? []) await action.run({ RegQID: 14 });
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith(
      "/api/regression/questions/14/active",
      "PATCH",
      { is_active: true },
    );
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith(
      "/api/regression/questions/14/active",
      "PATCH",
      { is_active: false },
    );

    await button(wrapper, "Chạy regression").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Batch b-1: 4/5 đạt");
    expect(vi.mocked(api.apiSend)).toHaveBeenCalledWith("/api/regression/run", "POST", {});
  });
});
