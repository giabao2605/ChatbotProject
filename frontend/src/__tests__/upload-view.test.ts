import { mount, flushPromises } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import UploadView from "@/views/UploadView.vue";
import * as api from "@/api/client";
import { setLocale } from "@/i18n";

const push = vi.fn();

vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("@/api/client", () => ({
  apiGet: vi.fn().mockResolvedValue({ departments: [], sites: [] }),
  apiUpload: vi.fn(),
}));

function mountUploadView() {
  return mount(UploadView, {
    global: {
      stubs: {
        Button: { template: '<button type="submit"><slot /></button>' },
        Card: { template: '<div><slot name="content" /></div>' },
        InputText: {
          props: ["modelValue"],
          emits: ["update:modelValue"],
          template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
        },
        Message: { template: '<div class="message"><slot /></div>' },
      },
    },
  });
}

describe("UploadView validation", () => {
  it("updates the worker warning and stops polling on unmount", async () => {
    vi.useFakeTimers();
    let status = "unavailable";
    vi.mocked(api.apiGet).mockImplementation(async (path) => (
      path === "/api/health" ? { ingestion_worker: status } : { departments: [], sites: [] }
    ) as never);
    const wrapper = mountUploadView();
    try {
      await flushPromises();
      expect(wrapper.text()).toContain("Chưa xác nhận được bộ xử lý");
      status = "ready";
      await vi.advanceTimersByTimeAsync(15000);
      expect(wrapper.text()).not.toContain("Chưa xác nhận được bộ xử lý");
      status = "unavailable";
      await vi.advanceTimersByTimeAsync(15000);
      expect(wrapper.text()).toContain("Chưa xác nhận được bộ xử lý");
      wrapper.unmount();
      const calls = vi.mocked(api.apiGet).mock.calls.length;
      await vi.advanceTimersByTimeAsync(15000);
      expect(vi.mocked(api.apiGet).mock.calls.length).toBe(calls);
    } finally {
      wrapper.unmount();
      vi.useRealTimers();
    }
  });
  it("requires a file before uploading", async () => {
    const wrapper = mountUploadView();

    await wrapper.find("form").trigger("submit");

    expect(wrapper.text()).toContain("Vui lòng chọn tệp");
    expect(api.apiUpload).not.toHaveBeenCalled();
  });

  it("requires department metadata when a file is selected", async () => {
    const wrapper = mountUploadView();
    const input = wrapper.find('input[type="file"]');
    Object.defineProperty(input.element, "files", {
      value: [new File(["abc"], "doc.pdf", { type: "application/pdf" })],
    });

    await input.trigger("change");
    await wrapper.find("form").trigger("submit");

    expect(wrapper.text()).toContain("Vui lòng nhập phòng ban");
    expect(api.apiUpload).not.toHaveBeenCalled();
  });

  it("uploads valid form data", async () => {
    vi.mocked(api.apiUpload).mockResolvedValue({ job_id: 55 });
    const wrapper = mountUploadView();
    const input = wrapper.find('input[type="file"]');
    Object.defineProperty(input.element, "files", {
      value: [new File(["abc"], "doc.pdf", { type: "application/pdf" })],
    });

    await input.trigger("change");
    const textInputs = wrapper.findAll('input:not([type="file"]):not([type="radio"]):not([type="checkbox"])');
    await textInputs.at(0)?.setValue("CoKhi");
    await wrapper.find("form").trigger("submit");

    expect(api.apiUpload).toHaveBeenCalledOnce();
    const form = vi.mocked(api.apiUpload).mock.calls[0][1] as FormData;
    expect(form.get("file")).toBeInstanceOf(File);
    expect(form.get("thu_muc")).toBe("CoKhi");
    expect(wrapper.text()).toContain("Đã tạo job ingest #55");
  });
});

it("translates the upload form and opens its file picker from a named button", async () => {
  setLocale("en");
  const wrapper = mountUploadView();
  try {
    await flushPromises();
    expect(wrapper.text()).toContain("Files to ingest");
    expect(wrapper.text()).toContain("Shared details for this upload batch");
    expect(wrapper.text()).not.toContain("Chia sẻ thêm");
    expect(wrapper.text()).not.toContain("Chưa chọn file");
    const picker = wrapper.get<HTMLInputElement>('input[type="file"]');
    const click = vi.spyOn(picker.element, "click").mockImplementation(() => {});
    const choose = wrapper.findAll("button").find(button => button.text() === "Choose files");
    expect(choose).toBeDefined();
    expect(choose!.attributes("type")).toBe("button");
    await choose!.trigger("click");
    expect(click).toHaveBeenCalledOnce();
  } finally { wrapper.unmount(); setLocale("vi"); }
});
