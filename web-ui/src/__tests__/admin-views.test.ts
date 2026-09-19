import { defineComponent, h, type PropType } from "vue";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import OrgView from "@/views/OrgView.vue";
import SettingsView from "@/views/SettingsView.vue";
import * as api from "@/api/client";
import type {
  ApiRow,
  CreateForm,
  ResourceColumn,
  ResourceFilter,
  RowAction,
  ToolbarAction,
} from "@/types";

const authState = vi.hoisted(() => ({
  user: { roles: ["platform_admin"] as string[] } as { roles: string[] } | null,
}));

vi.mock("@/api/client", () => ({
  apiGet: vi.fn(),
  apiSend: vi.fn(),
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: () => ({
    get user() {
      return authState.user;
    },
  }),
}));

const ButtonStub = defineComponent({
  inheritAttrs: false,
  props: {
    label: { type: String, default: "" },
    type: { type: String, default: "button" },
    disabled: Boolean,
    loading: Boolean,
  },
  emits: ["click"],
  setup(props, { attrs, emit }) {
    return () =>
      h(
        "button",
        {
          ...attrs,
          type: props.type,
          disabled: props.disabled,
          "data-loading": String(props.loading),
          onClick: () => emit("click"),
        },
        props.label,
      );
  },
});

const InputTextStub = defineComponent({
  inheritAttrs: false,
  props: {
    modelValue: { type: [String, Number], default: "" },
    type: { type: String, default: "text" },
  },
  emits: ["update:modelValue"],
  setup(props, { attrs, emit }) {
    return () =>
      h("input", {
        ...attrs,
        type: props.type,
        value: props.modelValue,
        onInput: (event: Event) =>
          emit("update:modelValue", (event.target as HTMLInputElement).value),
      });
  },
});

const CardStub = defineComponent({
  setup(_props, { slots }) {
    return () =>
      h("article", { class: "card" }, [
        slots.title?.(),
        slots.subtitle?.(),
        slots.content?.(),
        slots.default?.(),
      ]);
  },
});

const DialogStub = defineComponent({
  props: {
    visible: Boolean,
    header: { type: String, default: "" },
  },
  emits: ["update:visible", "hide"],
  setup(props, { slots }) {
    return () =>
      props.visible
        ? h("div", { class: "dialog", "data-header": props.header }, [
            h("h2", props.header),
            slots.default?.(),
            h("footer", slots.footer?.()),
          ])
        : null;
  },
});

const ResourcePageStub = defineComponent({
  name: "ResourcePage",
  props: {
    title: { type: String, required: true },
    eyebrow: String,
    description: String,
    load: {
      type: Function as PropType<
        (filters?: Record<string, unknown>) => Promise<ApiRow[]>
      >,
      required: true,
    },
    columns: Array as PropType<ResourceColumn[]>,
    rowActions: Array as PropType<RowAction[]>,
    toolbar: Array as PropType<ToolbarAction[]>,
    createForm: Object as PropType<CreateForm>,
    filters: Array as PropType<ResourceFilter[]>,
  },
  setup(props) {
    return () =>
      h("section", { class: "resource-page", "data-title": props.title }, props.title);
  },
});

const global = {
  stubs: {
    Button: ButtonStub,
    Card: CardStub,
    Dialog: DialogStub,
    InputText: InputTextStub,
    Message: { template: '<div class="message"><slot /></div>' },
    ProgressSpinner: { template: '<div class="spinner">loading</div>' },
    ResourcePage: ResourcePageStub,
  },
};

type ResourceHarness = {
  title: string;
  load: (filters?: Record<string, unknown>) => Promise<ApiRow[]>;
  columns?: ResourceColumn[];
  rowActions?: RowAction[];
  toolbar?: ToolbarAction[];
  createForm?: CreateForm;
  filters?: ResourceFilter[];
};

type RouteValue =
  | unknown
  | ((params?: Record<string, unknown>) => unknown | Promise<unknown>);

function mockGet(routes: Record<string, RouteValue>) {
  const implementation = async (
    path: string,
    params?: Record<string, unknown>,
  ): Promise<unknown> => {
    if (!(path in routes)) throw new Error(`Unexpected GET ${path}`);
    const value = routes[path];
    return typeof value === "function"
      ? (value as (query?: Record<string, unknown>) => unknown)(params)
      : value;
  };
  vi.mocked(api.apiGet).mockImplementation(
    implementation as typeof api.apiGet,
  );
}

function resource(wrapper: VueWrapper, title: string): ResourceHarness {
  const found = wrapper
    .findAllComponents(ResourcePageStub)
    .find((item) => item.props("title") === title);
  expect(found, `ResourcePage "${title}"`).toBeDefined();
  return found!.props() as unknown as ResourceHarness;
}

function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll("button").find((item) => item.text() === label);
  expect(found, `button "${label}"`).toBeDefined();
  return found!;
}

function field(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll("label").find((item) => {
    const first = item.find("span");
    return first.exists() && first.text() === label;
  });
  expect(found, `field "${label}"`).toBeDefined();
  return found!;
}

function defaultOrgRoutes(): Record<string, RouteValue> {
  return {
    "/api/catalog/departments": {
      departments: [
        { code: "ENG", name: "Engineering" },
        { DeptCode: "HR", DeptName: "" },
        { code: "", name: "Ignored" },
      ],
    },
    "/api/catalog/sites": {
      sites: [{ code: "A", name: "Plant A" }],
    },
    "/api/catalog/knowledge-governance": {
      governance: [{ department_code: "ENG" }],
    },
    "/api/catalog/domain-profiles": {
      profiles: [{ department_code: "ENG" }],
    },
    "/api/catalog/missing-site-documents": {
      documents: [{ doc_id: 11 }],
    },
    "/api/catalog/rollout/readiness": {
      departments: [
        {
          department_code: "ENG",
          wave_number: 1,
          rollout_status: "planned",
          prerequisites: { taxonomy: false },
        },
        {
          department_code: "HR",
          wave_number: 2,
          rollout_status: "blocked",
          missing_prerequisites: [],
        },
      ],
    },
  };
}

beforeEach(() => {
  authState.user = { roles: ["platform_admin"] };
  vi.clearAllMocks();
  vi.mocked(api.apiSend).mockResolvedValue({});
});

describe("OrgView public ResourcePage contracts", () => {
  it("exposes fail-closed loaders, filters, columns, and role-gated forms", async () => {
    mockGet(defaultOrgRoutes());
    const wrapper = mount(OrgView, { global });
    await flushPromises();

    expect(wrapper.findAllComponents(ResourcePageStub)).toHaveLength(7);
    expect(
      await resource(wrapper, "Phòng ban").load(),
    ).toEqual([
      { code: "ENG", name: "Engineering" },
      { DeptCode: "HR", DeptName: "" },
      { code: "", name: "Ignored" },
    ]);
    expect(await resource(wrapper, "Site").load()).toEqual([
      { code: "A", name: "Plant A" },
    ]);
    expect(await resource(wrapper, "Knowledge governance").load()).toEqual([
      { department_code: "ENG" },
    ]);
    expect(await resource(wrapper, "Domain profiles").load()).toEqual([
      { department_code: "ENG" },
    ]);
    expect(await resource(wrapper, "Tài liệu thiếu site").load()).toEqual([
      { doc_id: 11 },
    ]);

    const rollout = resource(wrapper, "Rollout readiness");
    expect(
      await rollout.load({ wave_number: 1, rollout_status: "PLANNED" }),
    ).toEqual([
      expect.objectContaining({
        department_code: "ENG",
        servable_document_count: 0,
        missing_prerequisites: ["taxonomy"],
        missing_prerequisites_display: "Taxonomy",
      }),
    ]);
    expect(rollout.filters).toEqual([
      expect.objectContaining({ key: "wave_number" }),
      expect.objectContaining({ key: "rollout_status" }),
    ]);
    expect(rollout.columns).toContainEqual(
      expect.objectContaining({ field: "ready_for_next_wave", kind: "bool" }),
    );

    for (const title of [
      "Knowledge governance",
      "Domain profiles",
      "Rollout readiness",
      "Evaluation gate",
    ]) {
      expect(resource(wrapper, title).createForm).toBeDefined();
    }

    expect(api.apiGet).toHaveBeenCalledWith("/api/catalog/departments", {
      active_only: false,
    });
    expect(api.apiGet).toHaveBeenCalledWith(
      "/api/catalog/missing-site-documents",
      { limit: 500 },
    );

    authState.user = { roles: ["viewer"] };
    const restricted = mount(OrgView, { global });
    await flushPromises();
    for (const title of [
      "Knowledge governance",
      "Domain profiles",
      "Rollout readiness",
      "Evaluation gate",
    ]) {
      expect(resource(restricted, title).createForm).toBeUndefined();
    }
    expect(resource(restricted, "Phòng ban").createForm).toBeDefined();
    expect(resource(restricted, "Tài liệu thiếu site").createForm).toBeDefined();
  });

  it("submits every public create form with normalized paths and payloads", async () => {
    mockGet(defaultOrgRoutes());
    const wrapper = mount(OrgView, { global });
    await flushPromises();

    await resource(wrapper, "Phòng ban").createForm!.submit({
      code: "ENG",
      name: "Engineering",
    });
    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/catalog/departments",
      "POST",
      { code: "ENG", name: "Engineering" },
    );

    await resource(wrapper, "Site").createForm!.submit({
      code: "A",
      name: "Plant A",
    });
    expect(api.apiSend).toHaveBeenCalledWith("/api/catalog/sites", "POST", {
      code: "A",
      name: "Plant A",
    });

    await resource(wrapper, "Knowledge governance").createForm!.submit({
      department_code: " ENG / R&D ",
      knowledge_owner_user_id: 4,
      knowledge_approver_user_id: 5,
      taxonomy_version: "v2",
      external_processing_policy: "",
    });
    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/catalog/departments/ENG%20%2F%20R%26D/knowledge-governance",
      "PUT",
      {
        knowledge_owner_user_id: 4,
        knowledge_approver_user_id: 5,
        taxonomy_version: "v2",
        external_processing_policy: "all_external",
        is_active: true,
      },
    );

    await resource(wrapper, "Domain profiles").createForm!.submit({
      department_code: "ENG",
      document_types: " manual, drawing, ,",
      required_metadata: "owner, revision",
      router_patterns: null,
      disable_parent_context: true,
    });
    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/catalog/departments/ENG/domain-profile",
      "PUT",
      {
        document_types: ["manual", "drawing"],
        required_metadata: ["owner", "revision"],
        router_patterns: [],
        parent_context_enabled: false,
        is_active: true,
      },
    );

    await resource(wrapper, "Tài liệu thiếu site").createForm!.submit({
      doc_id: " 11 ",
      site: "A",
    });
    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/documents/11/site",
      "PATCH",
      { site: "A" },
    );

    await resource(wrapper, "Rollout readiness").createForm!.submit({
      department_code: "ENG",
      wave_number: 2,
      rollout_status: "",
      evaluation_question_target: 0,
    });
    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/catalog/departments/ENG/rollout-plan",
      "PUT",
      {
        wave_number: 2,
        rollout_status: "planned",
        evaluation_question_target: 75,
      },
    );

    await resource(wrapper, "Evaluation gate").createForm!.submit({
      department_code: "ENG",
      batch_id: "pilot-1",
      question_count: 80,
      source_top5_rate: 0.99,
      citation_or_refusal_rate: 0.98,
      evidence_support_rate: 0.97,
      rbac_site_publication_leaks: 0,
      notes: "",
    });
    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/catalog/departments/ENG/evaluation-gate",
      "POST",
      {
        batch_id: "pilot-1",
        question_count: 80,
        source_top5_rate: 0.99,
        citation_or_refusal_rate: 0.98,
        evidence_support_rate: 0.97,
        rbac_site_publication_leaks: 0,
        notes: null,
      },
    );
  });

  it("runs site and department actions through their documented endpoints", async () => {
    mockGet(defaultOrgRoutes());
    const wrapper = mount(OrgView, { global });
    await flushPromises();

    const siteActions = resource(wrapper, "Site").rowActions!;
    const activate = siteActions.find((action) => action.label === "Kích hoạt")!;
    const deactivate = siteActions.find(
      (action) => action.label === "Vô hiệu hóa",
    )!;
    expect(activate.visible!({ is_active: false })).toBe(true);
    expect(activate.visible!({ is_active: 0 })).toBe(true);
    expect(activate.visible!({ is_active: true })).toBe(false);

    await activate.run({ SiteCode: "A", SiteName: "Plant A" });
    expect(api.apiSend).toHaveBeenCalledWith("/api/catalog/sites", "POST", {
      code: "A",
      name: "Plant A",
      is_active: true,
    });
    await deactivate.run({ code: "A", name: "Plant A" });
    expect(api.apiSend).toHaveBeenCalledWith("/api/catalog/sites", "POST", {
      code: "A",
      name: "Plant A",
      is_active: false,
    });

    const archive = resource(wrapper, "Phòng ban").rowActions!.find(
      (action) => action.label === "Lưu trữ",
    )!;
    await archive.run({ DepartmentCode: "ENG" });
    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/catalog/departments/ENG/archive",
      "POST",
      { force: false },
    );
  });

  it("validates, saves, retries, and cancels department dialogs", async () => {
    mockGet(defaultOrgRoutes());
    const wrapper = mount(OrgView, { global });
    await flushPromises();
    const actions = resource(wrapper, "Phòng ban").rowActions!;
    const changeStatus = actions.find(
      (action) => action.label === "Đổi trạng thái",
    )!;
    const reassign = actions.find(
      (action) => action.label === "Chuyển dữ liệu",
    )!;

    const statusDone = changeStatus.run({ code: "ENG", status: "disabled" });
    await flushPromises();
    expect(wrapper.find(".dialog").attributes("data-header")).toBe(
      "Đổi trạng thái — ENG",
    );
    await wrapper.find(".dialog select").setValue("active");
    await button(wrapper, "Lưu").trigger("click");
    await statusDone;
    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/catalog/departments/ENG/status",
      "PATCH",
      { status: "active" },
    );
    expect(wrapper.find(".dialog").exists()).toBe(false);

    const statusRetry = changeStatus.run({ MaPhong: "HR", Status: "" });
    await flushPromises();
    vi.mocked(api.apiSend).mockRejectedValueOnce(new Error("status offline"));
    await button(wrapper, "Lưu").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("status offline");
    vi.mocked(api.apiSend).mockRejectedValueOnce("opaque");
    await button(wrapper, "Lưu").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Lỗi");
    vi.mocked(api.apiSend).mockResolvedValueOnce({});
    await button(wrapper, "Lưu").trigger("click");
    await statusRetry;

    const reassignDone = reassign.run({ DepartmentCode: "ENG" });
    await flushPromises();
    expect(wrapper.find(".dialog").attributes("data-header")).toBe(
      "Chuyển dữ liệu — ENG",
    );
    expect(
      wrapper
        .findAll(".dialog option")
        .map((option) => option.attributes("value")),
    ).toEqual(["", "HR"]);
    await button(wrapper, "Lưu").trigger("click");
    expect(wrapper.text()).toContain("Chọn phòng ban đích.");
    await wrapper.find(".dialog select").setValue("HR");
    await wrapper.find('.dialog input[type="checkbox"]').setValue(false);
    await button(wrapper, "Lưu").trigger("click");
    await reassignDone;
    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/catalog/departments/reassign",
      "POST",
      {
        source_code: "ENG",
        target_code: "HR",
        move_users: false,
      },
    );

    const cancelled = reassign.run({ code: "ENG" });
    const cancelledAssertion = expect(cancelled).rejects.toThrow("");
    await flushPromises();
    await button(wrapper, "Huỷ").trigger("click");
    await cancelledAssertion;
    expect(wrapper.find(".dialog").exists()).toBe(false);
  });

  it("keeps optional catalogs empty when the department preload fails", async () => {
    mockGet({
      ...defaultOrgRoutes(),
      "/api/catalog/departments": () => {
        throw new Error("catalog offline");
      },
    });
    const wrapper = mount(OrgView, { global });
    await flushPromises();

    const reassign = resource(wrapper, "Phòng ban").rowActions!.find(
      (action) => action.label === "Chuyển dữ liệu",
    )!;
    const cancelled = reassign.run({ code: "ENG" });
    const cancelledAssertion = expect(cancelled).rejects.toThrow("");
    await flushPromises();
    expect(wrapper.findAll(".dialog option")).toHaveLength(1);
    await button(wrapper, "Huỷ").trigger("click");
    await cancelledAssertion;
  });
});

type PolicyProfile = {
  provider: string;
  endpoint: string;
  default_model: string;
  secret_reference: string;
  allowed_surfaces: string[];
  retention_mode: string;
  policy_version: string;
  approved_by: string;
  risk_acceptance_ref: string;
  review_expires_at: string | null;
  review_state: "current" | "expired" | "unknown";
  is_active: boolean;
};

const currentProfile: PolicyProfile = {
  provider: "voyage",
  endpoint: "https://api.voyageai.com",
  default_model: "rerank-2.5-lite",
  secret_reference: "env:VOYAGE_API_KEY",
  allowed_surfaces: ["retrieval", "rerank"],
  retention_mode: "zero",
  policy_version: "v1",
  approved_by: "security",
  risk_acceptance_ref: "RA-1",
  review_expires_at: "2027-01-02T00:00:00Z",
  review_state: "current",
  is_active: true,
};

function defaultSettingsRoutes(
  profiles: PolicyProfile[] = [currentProfile],
): Record<string, RouteValue> {
  return {
    "/api/settings/external-ai-policy": { profiles },
    "/api/settings": {
      settings: { theme: "dark", retries: 3 },
    },
  };
}

describe("SettingsView public behavior", () => {
  it("renders all policy states and exposes settings load/create contracts", async () => {
    mockGet(
      defaultSettingsRoutes([
        currentProfile,
        {
          ...currentProfile,
          provider: "expired",
          review_state: "expired",
        },
        {
          ...currentProfile,
          provider: "disabled",
          is_active: false,
        },
      ]),
    );
    const wrapper = mount(SettingsView, { global });
    await flushPromises();

    expect(wrapper.text()).toContain("Đang áp dụng");
    expect(wrapper.text()).toContain("Cần review");
    expect(wrapper.text()).toContain("Đã tắt");
    expect(wrapper.text()).toContain("retrieval, rerank");
    expect(wrapper.text()).toContain("2027-01-02T00:00:00Z");

    const settings = resource(wrapper, "Cấu hình");
    expect(await settings.load()).toEqual([
      { key: "theme", value: "dark" },
      { key: "retries", value: 3 },
    ]);
    await settings.createForm!.submit({ key: "rag / timeout", value: "30" });
    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/settings/rag%20%2F%20timeout",
      "PUT",
      { value: "30" },
    );
    expect(settings.columns).toEqual([
      { field: "key", header: "Khóa" },
      { field: "value", header: "Giá trị" },
    ]);
  });

  it("edits external AI policy, normalizes surfaces, retries errors, and cancels", async () => {
    mockGet(defaultSettingsRoutes());
    const wrapper = mount(SettingsView, { global });
    await flushPromises();

    await button(wrapper, "Cập nhật").trigger("click");
    expect(wrapper.find(".dialog").attributes("data-header")).toBe(
      "Cập nhật external AI policy",
    );
    expect(field(wrapper, "Provider").find("input").attributes("disabled")).toBeDefined();
    await field(wrapper, "Endpoint").find("input").setValue("https://proxy.example");
    await field(wrapper, "Default model").find("input").setValue("rerank-v3");
    await field(wrapper, "Secret reference")
      .find("input")
      .setValue("env:RERANK_KEY");
    await field(wrapper, "Allowed surfaces, ngăn cách dấu phẩy")
      .find("textarea")
      .setValue(" chat, , search, retrieval ");
    await field(wrapper, "Retention mode").find("input").setValue("none");
    await field(wrapper, "Policy version").find("input").setValue("v2");
    await field(wrapper, "Approved by").find("input").setValue("risk-board");
    await field(wrapper, "Risk acceptance reference")
      .find("input")
      .setValue("RA-2");
    await field(wrapper, "Review expiry")
      .find('input[type="date"]')
      .setValue("2027-03-04");
    await wrapper.find('.dialog input[type="checkbox"]').setValue(false);
    await wrapper.find(".dialog form").trigger("submit");
    await flushPromises();

    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/settings/external-ai-policy/voyage",
      "PUT",
      {
        endpoint: "https://proxy.example",
        default_model: "rerank-v3",
        secret_reference: "env:RERANK_KEY",
        allowed_surfaces: ["chat", "search", "retrieval"],
        retention_mode: "none",
        policy_version: "v2",
        approved_by: "risk-board",
        risk_acceptance_ref: "RA-2",
        review_expires_at: "2027-03-04",
        is_active: false,
      },
    );
    expect(api.apiGet).toHaveBeenCalledTimes(2);
    expect(wrapper.find(".dialog").exists()).toBe(false);

    await button(wrapper, "Cập nhật").trigger("click");
    vi.mocked(api.apiSend).mockRejectedValueOnce(new Error("policy denied"));
    await wrapper.find(".dialog form").trigger("submit");
    await flushPromises();
    expect(wrapper.text()).toContain("policy denied");
    expect(wrapper.find(".dialog").exists()).toBe(true);

    vi.mocked(api.apiSend).mockRejectedValueOnce("opaque");
    await wrapper.find(".dialog form").trigger("submit");
    await flushPromises();
    expect(wrapper.text()).toContain("Không lưu được external AI policy");
    await button(wrapper, "Huỷ").trigger("click");
    expect(wrapper.find(".dialog").exists()).toBe(false);
  });

  it("loads policy fail-closed for empty, Error, and opaque failures", async () => {
    mockGet(defaultSettingsRoutes([]));
    const empty = mount(SettingsView, { global });
    await flushPromises();
    expect(empty.text()).toContain("Chưa có provider profile");

    mockGet({
      ...defaultSettingsRoutes(),
      "/api/settings/external-ai-policy": () => {
        throw new Error("policy offline");
      },
    });
    const failed = mount(SettingsView, { global });
    await flushPromises();
    expect(failed.text()).toContain("policy offline");

    mockGet({
      ...defaultSettingsRoutes(),
      "/api/settings/external-ai-policy": () => {
        throw "opaque";
      },
    });
    const opaque = mount(SettingsView, { global });
    await flushPromises();
    expect(opaque.text()).toContain("Không tải được external AI policy");
  });

  it("saves, retries, and cancels the setting-value dialog", async () => {
    mockGet(defaultSettingsRoutes());
    const wrapper = mount(SettingsView, { global });
    await flushPromises();
    const edit = resource(wrapper, "Cấu hình").rowActions![0];

    const done = edit.run({ key: "rag/timeout", value: 30 });
    await flushPromises();
    expect(wrapper.find(".dialog").attributes("data-header")).toBe(
      "Sửa: rag/timeout",
    );
    expect(
      (wrapper.find(".dialog textarea").element as HTMLTextAreaElement).value,
    ).toBe("30");
    await wrapper.find(".dialog textarea").setValue("45");
    vi.mocked(api.apiSend).mockRejectedValueOnce(new Error("write denied"));
    await button(wrapper, "Lưu").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("write denied");

    vi.mocked(api.apiSend).mockRejectedValueOnce("opaque");
    await button(wrapper, "Lưu").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Lỗi");

    vi.mocked(api.apiSend).mockResolvedValueOnce({});
    await button(wrapper, "Lưu").trigger("click");
    await done;
    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/settings/rag%2Ftimeout",
      "PUT",
      { value: "45" },
    );
    expect(wrapper.find(".dialog").exists()).toBe(false);

    const cancelled = edit.run({ key: null, value: null });
    const cancelledAssertion = expect(cancelled).rejects.toThrow("");
    await flushPromises();
    expect(wrapper.find(".dialog").attributes("data-header")).toBe("Sửa: ");
    await button(wrapper, "Huỷ").trigger("click");
    await cancelledAssertion;
  });
});
