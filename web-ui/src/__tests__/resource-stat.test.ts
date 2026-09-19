import {
  computed,
  defineComponent,
  h,
  inject,
  nextTick,
  provide,
  type ComputedRef,
} from "vue";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ResourcePage from "@/components/ResourcePage.vue";
import StatView from "@/components/StatView.vue";
import { setLocale } from "@/i18n";
import type {
  ApiRow,
  CreateForm,
  ResourceColumn,
  ResourceFilter,
  RowAction,
  ToolbarAction,
} from "@/types";

const TABLE_ROWS = Symbol("tableRows");

const ButtonStub = defineComponent({
  name: "Button",
  inheritAttrs: false,
  props: {
    label: { type: String, default: "" },
    type: { type: String, default: "button" },
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
          type: props.type,
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
  setup(_props, { slots }) {
    return () =>
      h("article", { class: "card" }, [
        slots.title?.(),
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
  emits: ["update:visible"],
  setup(props, { slots }) {
    return () =>
      props.visible
        ? h("div", { class: "dialog" }, [
            h("h2", props.header),
            slots.default?.(),
          ])
        : null;
  },
});

const DataTableStub = defineComponent({
  props: {
    value: { type: Array, default: () => [] },
  },
  setup(props, { slots }) {
    provide(TABLE_ROWS, computed(() => props.value));
    return () => h("div", { class: "data-table" }, slots.default?.());
  },
});

const ColumnStub = defineComponent({
  props: {
    field: { type: String, default: "" },
    header: { type: String, default: "" },
  },
  setup(props, { slots }) {
    const rows = inject<ComputedRef<unknown[]>>(TABLE_ROWS, computed(() => []));
    return () =>
      h("section", { class: "column", "data-field": props.field }, [
        h("b", { class: "column-header" }, props.header),
        ...rows.value.map((data, index) =>
          h(
            "div",
            { class: "table-cell", "data-row": String(index) },
            slots.body?.({ data }),
          ),
        ),
      ]);
  },
});

const global = {
  stubs: {
    Button: ButtonStub,
    Card: CardStub,
    Column: ColumnStub,
    DataTable: DataTableStub,
    Dialog: DialogStub,
    InputText: ModelInputStub,
    Message: { template: '<div class="message"><slot /></div>' },
    ProgressSpinner: { template: '<div class="spinner">loading</div>' },
    Tag: {
      props: ["value"],
      template: '<span class="tag">{{ value }}</span>',
    },
    Textarea: TextareaStub,
  },
};

type ResourceProps = {
  title: string;
  eyebrow?: string;
  description?: string;
  load: (filters: Record<string, unknown>) => Promise<ApiRow[]>;
  columns?: ResourceColumn[];
  rowActions?: RowAction[];
  toolbar?: ToolbarAction[];
  createForm?: CreateForm;
  filters?: ResourceFilter[];
};

type StatProps = {
  title: string;
  eyebrow?: string;
  description?: string;
  load: () => Promise<unknown>;
  toolbar?: ToolbarAction[];
};

function mountResource(props: ResourceProps) {
  return mount(ResourcePage, { props, global });
}

function mountStat(props: StatProps) {
  return mount(StatView, { props, global });
}

function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll("button").find((item) => item.text() === label);
  expect(found, `button "${label}"`).toBeDefined();
  return found!;
}

beforeEach(() => {
  setLocale("vi");
  vi.restoreAllMocks();
});

describe("ResourcePage public behavior", () => {
  it("loads object rows and formats every supported cell kind", async () => {
    let resolveLoad!: (rows: ApiRow[]) => void;
    const load = vi.fn(
      () =>
        new Promise<ApiRow[]>((resolve) => {
          resolveLoad = resolve;
        }),
    );
    const columns: ResourceColumn[] = [
      { field: "active", header: "Active", kind: "bool" },
      { field: "inactive", header: "Inactive", kind: "bool" },
      { field: "score", header: "Score", kind: "score" },
      { field: "meta", header: "Meta" },
      { field: "missing", header: "Missing" },
      { field: "code", header: "Code", kind: "code" },
      { field: "state", header: "State", kind: "tag" },
    ];
    const wrapper = mountResource({
      title: "Máy",
      description: "Danh sách máy",
      load,
      columns,
    });

    await nextTick();
    expect(wrapper.find(".spinner").exists()).toBe(true);
    resolveLoad([
      {
        active: true,
        inactive: false,
        score: 1.23456,
        meta: { family: "CNC" },
        missing: null,
        code: "M-01",
        state: "ready",
      },
    ]);
    await flushPromises();

    expect(load).toHaveBeenCalledWith({});
    expect(wrapper.text()).toContain("Operations");
    expect(wrapper.text()).toContain("Danh sách máy");
    expect(wrapper.text()).toContain("Có");
    expect(wrapper.text()).toContain("Không");
    expect(wrapper.text()).toContain("1.235");
    expect(wrapper.text()).toContain('{"family":"CNC"}');
    expect(wrapper.text()).toContain("—");
    expect(wrapper.find("code").text()).toBe("M-01");
    expect(wrapper.find(".tag").text()).toBe("ready");
  });

  it("normalizes positional and named rows and renders empty data", async () => {
    const positional = vi
      .fn<(filters: Record<string, unknown>) => Promise<ApiRow[]>>()
      .mockResolvedValueOnce(
        [
          ["P-1", 12],
          ["P-2", 8],
        ] as unknown as ApiRow[],
      )
      .mockResolvedValueOnce([]);
    const positionalWrapper = mountResource({
      title: "Parts",
      eyebrow: "Inventory",
      load: positional,
    });
    await flushPromises();

    expect(positionalWrapper.text()).toContain("Inventory");
    expect(positionalWrapper.text()).toContain("#1");
    expect(positionalWrapper.text()).toContain("#2");
    expect(positionalWrapper.text()).toContain("P-2");
    await button(positionalWrapper, "Tải lại").trigger("click");
    await flushPromises();
    expect(positionalWrapper.text()).toContain("Không có dữ liệu");

    const namedWrapper = mountResource({
      title: "Named",
      load: vi.fn().mockResolvedValue([{ part_no: "P-3", revision_id: 4 }]),
    });
    await flushPromises();
    expect(namedWrapper.text()).toContain("Part No");
    expect(namedWrapper.text()).toContain("Revision Id");
  });

  it("reloads when each filter changes and reports load failures", async () => {
    const load = vi
      .fn<(filters: Record<string, unknown>) => Promise<ApiRow[]>>()
      .mockResolvedValue([]);
    const wrapper = mountResource({
      title: "Filtered",
      load,
      filters: [
        { key: "query", label: "Query", value: "P" },
        {
          key: "state",
          label: "State",
          type: "select",
          options: [{ label: "Ready", value: "ready" }],
        },
        { key: "active", label: "Active", type: "checkbox" },
      ],
    });
    await flushPromises();

    expect(load).toHaveBeenNthCalledWith(1, {
      query: "P",
      state: "",
      active: false,
    });
    await wrapper.find('input[type="text"]').setValue("M");
    await wrapper.find("select").setValue("ready");
    await wrapper.find('input[type="checkbox"]').setValue(true);
    await flushPromises();
    expect(load).toHaveBeenLastCalledWith({
      query: "M",
      state: "ready",
      active: true,
    });

    load.mockRejectedValueOnce(new Error("network down"));
    await button(wrapper, "Tải lại").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("network down");

    load.mockRejectedValueOnce("opaque failure");
    await button(wrapper, "Tải lại").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Đã xảy ra lỗi");
  });

  it("confirms row and toolbar actions and reports their result", async () => {
    const load = vi.fn().mockResolvedValue([{ id: 7, state: "ready" }]);
    const runRow = vi.fn().mockResolvedValue(undefined);
    const runToolbar = vi.fn().mockResolvedValue(undefined);
    const failRow = vi.fn().mockRejectedValue(new Error("row failed"));
    const failToolbar = vi.fn().mockRejectedValue("toolbar failed");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const wrapper = mountResource({
      title: "Actions",
      load,
      columns: [{ field: "id", header: "Id" }],
      rowActions: [
        { label: "Run row", confirm: "Sure?", run: runRow },
        { label: "Fail row", run: failRow },
        { label: "Always", run: vi.fn().mockResolvedValue(undefined) },
        { label: "Hidden", visible: () => false, run: vi.fn() },
      ],
      toolbar: [
        { label: "Run toolbar", confirm: "Sure?", run: runToolbar },
        { label: "Fail toolbar", run: failToolbar },
      ],
    });
    await flushPromises();

    expect(wrapper.text()).not.toContain("Hidden");
    await button(wrapper, "Run row").trigger("click");
    expect(runRow).not.toHaveBeenCalled();
    expect(load).toHaveBeenCalledOnce();

    confirm.mockReturnValue(true);
    await button(wrapper, "Run row").trigger("click");
    await flushPromises();
    expect(runRow).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }));
    expect(load).toHaveBeenCalledTimes(2);
    expect(wrapper.text()).toContain("Thành công");

    await button(wrapper, "Fail row").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("row failed");

    confirm.mockReturnValue(false);
    await button(wrapper, "Run toolbar").trigger("click");
    expect(runToolbar).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    await button(wrapper, "Run toolbar").trigger("click");
    await flushPromises();
    expect(runToolbar).toHaveBeenCalledOnce();
    expect(load).toHaveBeenCalledTimes(3);

    await button(wrapper, "Fail toolbar").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Đã xảy ra lỗi");
  });

  it("opens, cancels, submits, and reports errors from the create form", async () => {
    const load = vi.fn().mockResolvedValue([]);
    const submit = vi.fn().mockResolvedValue(undefined);
    const createForm: CreateForm = {
      title: "Create machine",
      triggerLabel: "Add machine",
      fields: [
        { key: "name", label: "Name", required: true, placeholder: "Machine name" },
        { key: "count", label: "Count", type: "number" },
        { key: "enabled", label: "Enabled", type: "checkbox" },
        { key: "notes", label: "Notes", type: "textarea", help: "Optional" },
        {
          key: "site",
          label: "Site",
          type: "select",
          options: [{ label: "Plant A", value: "A" }],
        },
      ],
      submit,
    };
    const wrapper = mountResource({
      title: "Machines",
      load,
      createForm,
    });
    await flushPromises();

    await button(wrapper, "Add machine").trigger("click");
    expect(wrapper.find(".dialog").exists()).toBe(true);
    expect(wrapper.text()).toContain("Create machine");
    expect(wrapper.text()).toContain("Optional");
    await wrapper.find('input[type="text"]').setValue("Lathe");
    await wrapper.find('input[type="number"]').setValue("4");
    await wrapper.find('input[type="checkbox"]').setValue(true);
    await wrapper.find("textarea").setValue("Cell 2");
    await wrapper.find("select").setValue("A");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(submit).toHaveBeenCalledWith({
      name: "Lathe",
      count: "4",
      enabled: true,
      notes: "Cell 2",
      site: "A",
    });
    expect(wrapper.find(".dialog").exists()).toBe(false);
    expect(wrapper.text()).toContain("Thành công");
    expect(load).toHaveBeenCalledTimes(2);

    await button(wrapper, "Add machine").trigger("click");
    await button(wrapper, "Hủy").trigger("click");
    expect(wrapper.find(".dialog").exists()).toBe(false);

    submit.mockRejectedValueOnce(new Error("duplicate machine"));
    await button(wrapper, "Add machine").trigger("click");
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(wrapper.find(".dialog").exists()).toBe(true);
    expect(wrapper.text()).toContain("duplicate machine");

    submit.mockRejectedValueOnce("opaque failure");
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(wrapper.text()).toContain("Đã xảy ra lỗi");
  });
});

describe("StatView public behavior", () => {
  it("renders scalars, numeric bars, object tables, and inline metrics", async () => {
    const wrapper = mountStat({
      title: "Metrics",
      description: "Runtime statistics",
      load: vi.fn().mockResolvedValue({
        count: 3,
        ratio: 1.23,
        enabled: false,
        missing: null,
        numeric_map: { low: -2, high: "4", zero: 0 },
        nested_map: { machine: { id: 1 }, absent: null },
        rows: [
          { name: "A", score: 10, offset: -2, optional: null },
          { name: "B", score: "5", offset: 4, extra: false },
        ],
        values: [1, null, "x", Number.POSITIVE_INFINITY],
        skipped_array: [],
        blank_object: {},
      }),
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Operations");
    expect(wrapper.text()).toContain("Runtime statistics");
    expect(wrapper.text()).toContain("count");
    expect(wrapper.text()).toContain("3");
    expect(wrapper.text()).toContain("ratio");
    expect(wrapper.text()).toContain("1.23");
    expect(wrapper.text()).toContain("false");
    expect(wrapper.text()).toContain("—");

    const bars = wrapper.findAll(".bar-row");
    expect(bars.map((row) => row.find(".bar-label").text())).toEqual([
      "high",
      "low",
      "zero",
    ]);
    expect(bars.map((row) => row.find(".bar-fill").attributes("style"))).toEqual([
      "width: 100%;",
      "width: 50%;",
      "width: 0%;",
    ]);
    expect(wrapper.text()).toContain('{"id":1}');
    expect(wrapper.text()).toContain("Infinity");
    expect(wrapper.findAll(".cell-bar").some((cell) => cell.attributes("style") === "width: 100%;")).toBe(true);
    expect(wrapper.text()).not.toContain("skipped_array");
    expect(wrapper.text()).toContain("blank_object");
  });

  it("normalizes top-level object and primitive arrays", async () => {
    const objectWrapper = mountStat({
      title: "Object rows",
      eyebrow: "Analytics",
      load: vi.fn().mockResolvedValue([
        { name: "A", value: 2.5 },
        { name: "B", value: 5 },
      ]),
    });
    await flushPromises();
    expect(objectWrapper.text()).toContain("Analytics");
    expect(objectWrapper.text()).toContain("Object rows");
    expect(objectWrapper.text()).toContain("2.5");
    expect(objectWrapper.findAll(".cell-bar")).toHaveLength(2);

    const primitiveWrapper = mountStat({
      title: "Primitive rows",
      load: vi.fn().mockResolvedValue(["A", "B"]),
    });
    await flushPromises();
    expect(primitiveWrapper.text()).toContain("value");
    expect(primitiveWrapper.text()).toContain("A");
    expect(primitiveWrapper.text()).toContain("B");
  });

  it("uses readable labels for analytics field keys", async () => {
    const wrapper = mountStat({
      title: "Analytics",
      load: vi.fn().mockResolvedValue({
        today_questions: 3,
        cache_hit_rate: 0.8,
        p95_ms: 900,
      }),
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Câu hỏi hôm nay");
    expect(wrapper.text()).toContain("Tỷ lệ cache hit");
    expect(wrapper.text()).toContain("P95 (ms)");
    expect(wrapper.text()).not.toContain("today_questions");
    expect(wrapper.text()).not.toContain("cache_hit_rate");
  });

  it("shows loading and both load error forms, then recovers on refresh", async () => {
    let rejectLoad!: (reason: unknown) => void;
    const load = vi
      .fn<() => Promise<unknown>>()
      .mockImplementationOnce(
        () =>
          new Promise((_resolve, reject) => {
            rejectLoad = reject;
          }),
      )
      .mockRejectedValueOnce("opaque failure")
      .mockResolvedValueOnce({ recovered: true });
    const wrapper = mountStat({ title: "Errors", load });

    await nextTick();
    expect(wrapper.find(".spinner").exists()).toBe(true);
    rejectLoad(new Error("stats offline"));
    await flushPromises();
    expect(wrapper.text()).toContain("stats offline");

    await button(wrapper, "Tải lại").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Đã xảy ra lỗi");

    await button(wrapper, "Tải lại").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("recovered");
    expect(wrapper.text()).toContain("true");
  });

  it("confirms toolbar actions, refreshes on success, and reports failures", async () => {
    const load = vi.fn().mockResolvedValue({ jobs: 2 });
    const run = vi.fn().mockResolvedValue(undefined);
    const failError = vi.fn().mockRejectedValue(new Error("action failed"));
    const failOpaque = vi.fn().mockRejectedValue("opaque");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const wrapper = mountStat({
      title: "Toolbar",
      load,
      toolbar: [
        { label: "Rebuild", confirm: "Sure?", run },
        { label: "Fail error", run: failError },
        { label: "Fail opaque", run: failOpaque },
      ],
    });
    await flushPromises();

    await button(wrapper, "Rebuild").trigger("click");
    expect(run).not.toHaveBeenCalled();
    expect(load).toHaveBeenCalledOnce();

    confirm.mockReturnValue(true);
    await button(wrapper, "Rebuild").trigger("click");
    await flushPromises();
    expect(run).toHaveBeenCalledOnce();
    expect(load).toHaveBeenCalledTimes(2);

    await button(wrapper, "Fail error").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("action failed");

    await button(wrapper, "Fail opaque").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Đã xảy ra lỗi");
  });
});
