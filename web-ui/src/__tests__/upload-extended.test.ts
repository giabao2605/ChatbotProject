import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent, nextTick } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import UploadView from "@/views/UploadView.vue";
import * as api from "@/api/client";

const router = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("vue-router", () => ({
  useRouter: () => router,
}));

vi.mock("@/api/client", () => ({
  apiGet: vi.fn(),
  apiUpload: vi.fn(),
}));

const ButtonStub = defineComponent({
  props: { label: String, type: String, loading: Boolean },
  emits: ["click"],
  template:
    '<button :type="type || \'button\'" @click="$emit(\'click\')">{{ label }}</button>',
});

const InputStub = defineComponent({
  inheritAttrs: false,
  props: { modelValue: [String, Number], type: String },
  emits: ["update:modelValue"],
  template:
    '<input v-bind="$attrs" :type="type || \'text\'" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
});

function mountView() {
  return mount(UploadView, {
    global: {
      stubs: {
        Button: ButtonStub,
        Card: { template: '<div><slot name="content" /></div>' },
        InputText: InputStub,
        Message: { template: '<div class="message"><slot /></div>' },
      },
    },
  });
}

function setupState(wrapper: ReturnType<typeof mountView>) {
  return wrapper.vm as unknown as {
    uploadMode: "batch" | "per_file";
    meta: Record<string, string>;
    detail: Record<string, string>;
    assignments: Record<number, {
      thu_muc: string;
      extra_departments: string[];
    }>;
    batchExtraDepartments: string[];
  };
}

async function selectFiles(
  wrapper: ReturnType<typeof mountView>,
  files: File[],
) {
  const input = wrapper.find('input[type="file"]');
  Object.defineProperty(input.element, "files", {
    configurable: true,
    value: files,
  });
  await input.trigger("change");
}

function uploadedForm(call = 0): FormData {
  return vi.mocked(api.apiUpload).mock.calls[call][1] as FormData;
}

describe("UploadView extended behavior", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    vi.mocked(api.apiGet).mockImplementation((path) => Promise.resolve(
      path === "/api/catalog/departments"
        ? {
            departments: [
              { DeptCode: "ME", DeptName: "Cơ khí" },
              { code: "QA", name: "Chất lượng" },
              { code: "", name: "Bỏ qua" },
            ],
          }
        : {
            sites: [
              { SiteCode: "HCM", SiteName: "Nhà máy HCM" },
              { code: "", name: "Bỏ qua" },
            ],
          },
    ) as never);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("uploads one fully described file and redirects after success", async () => {
    vi.mocked(api.apiUpload).mockResolvedValue({ job_id: 71 });
    const wrapper = mountView();
    await flushPromises();
    const state = setupState(wrapper);
    await selectFiles(wrapper, [new File(["abc"], "pump.pdf")]);
    state.meta.thu_muc = "ME";
    state.meta.domain = " pumps ";
    state.meta.security_level = " internal ";
    state.meta.cong_doan = " assembly ";
    state.meta.site = " HCM ";
    state.batchExtraDepartments.push("QA");
    state.detail.title = " Pump manual ";
    state.detail.summary = " ";
    await nextTick();

    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(api.apiUpload).toHaveBeenCalledWith(
      "/api/documents/upload",
      expect.any(FormData),
    );
    const form = uploadedForm();
    expect(form.get("thu_muc")).toBe("ME");
    expect(form.get("domain")).toBe("pumps");
    expect(form.get("security_level")).toBe("internal");
    expect(form.get("extra_departments_json")).toBe('["QA"]');
    expect(JSON.parse(String(form.get("meta_json")))).toEqual({
      title: "Pump manual",
    });
    expect(wrapper.text()).toContain("Đã tạo job ingest #71");

    await vi.advanceTimersByTimeAsync(800);
    expect(router.push).toHaveBeenCalledWith("/queue");
  });

  it("uploads a batch and reports per-file failures without redirecting", async () => {
    vi.mocked(api.apiUpload).mockResolvedValue({
      created: 1,
      failed: 1,
      errors: [{ file_name: "bad.pdf", error: "OCR failed" }],
    });
    const wrapper = mountView();
    await flushPromises();
    const state = setupState(wrapper);
    await selectFiles(wrapper, [
      new File([new Uint8Array(2048)], "ok.pdf"),
      new File(["bad"], "bad.pdf"),
    ]);
    state.meta.thu_muc = "ME";
    state.meta.domain = " ";
    state.batchExtraDepartments.push("QA");

    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(api.apiUpload).toHaveBeenCalledWith(
      "/api/documents/upload-batch",
      expect.any(FormData),
    );
    const form = uploadedForm();
    expect(form.getAll("files")).toHaveLength(2);
    expect(form.get("thu_muc")).toBe("ME");
    expect(wrapper.text()).toContain("Đã tạo 1 job ingest, lỗi 1");
    expect(wrapper.text()).toContain("bad.pdf: OCR failed");
    await vi.advanceTimersByTimeAsync(800);
    expect(router.push).not.toHaveBeenCalled();
  });

  it("requires and serializes per-file department assignments", async () => {
    vi.mocked(api.apiUpload).mockResolvedValue({ created: 2, failed: 0 });
    const wrapper = mountView();
    await flushPromises();
    const state = setupState(wrapper);
    state.uploadMode = "per_file";
    await selectFiles(wrapper, [
      new File(["a"], "a.pdf"),
      new File(["b"], "b.pdf"),
    ]);
    state.assignments[1].thu_muc = "";

    await wrapper.find("form").trigger("submit");
    expect(wrapper.text()).toContain("Vui lòng chọn phòng ban cho từng file");
    expect(api.apiUpload).not.toHaveBeenCalled();

    state.assignments[0] = {
      thu_muc: "ME",
      extra_departments: ["QA"],
    };
    state.assignments[1] = {
      thu_muc: "QA",
      extra_departments: [],
    };
    state.detail.language = "vi";
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    const assignments = JSON.parse(String(uploadedForm().get(
      "assignments_json",
    )));
    expect(assignments).toEqual([
      { thu_muc: "ME", extra_departments: ["QA"] },
      { thu_muc: "QA", extra_departments: [] },
    ]);
    expect(wrapper.text()).toContain("Đã tạo 2 job ingest");
  });

  it("fails closed for catalog and upload errors", async () => {
    vi.mocked(api.apiGet).mockResolvedValueOnce({ ingestion_worker: "ready" });
    vi.mocked(api.apiGet).mockRejectedValueOnce(new Error("catalog offline"));
    const catalogFailure = mountView();
    await flushPromises();
    expect(catalogFailure.text()).toContain("catalog offline");

    vi.mocked(api.apiGet).mockResolvedValueOnce({ ingestion_worker: "ready" });
    vi.mocked(api.apiGet).mockRejectedValueOnce("offline");
    const nonErrorCatalog = mountView();
    await flushPromises();
    expect(nonErrorCatalog.text()).toContain("Đã xảy ra lỗi");

    vi.mocked(api.apiUpload).mockRejectedValueOnce(new Error("upload denied"));
    const wrapper = mountView();
    await flushPromises();
    const state = setupState(wrapper);
    await selectFiles(wrapper, [new File([], "empty.pdf")]);
    state.meta.thu_muc = "ME";
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(wrapper.text()).toContain("upload denied");

    vi.mocked(api.apiUpload).mockRejectedValueOnce("broken");
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(wrapper.text()).toContain("Đã xảy ra lỗi");
  });

  it("clears old assignments and supports direct queue navigation", async () => {
    const wrapper = mountView();
    await flushPromises();
    const state = setupState(wrapper);
    await selectFiles(wrapper, [new File(["a"], "a.pdf")]);
    state.assignments[0].extra_departments = ["QA"];
    await selectFiles(wrapper, []);
    expect(wrapper.text()).toContain("Chưa chọn file");
    expect(Object.keys(state.assignments)).toHaveLength(0);

    const queue = wrapper.findAll("button").find(
      (item) => item.text() === "Tiến trình ingest",
    );
    expect(queue).toBeDefined();
    await queue?.trigger("click");
    expect(router.push).toHaveBeenCalledWith("/queue");
  });
});
