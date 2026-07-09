import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import UploadView from "@/views/UploadView.vue";
import * as api from "@/api/client";

const push = vi.fn();

vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("@/api/client", () => ({
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
    await wrapper.findAll("input").at(1)?.setValue("CoKhi");
    await wrapper.find("form").trigger("submit");

    expect(api.apiUpload).toHaveBeenCalledOnce();
    const form = vi.mocked(api.apiUpload).mock.calls[0][1] as FormData;
    expect(form.get("file")).toBeInstanceOf(File);
    expect(form.get("thu_muc")).toBe("CoKhi");
    expect(wrapper.text()).toContain("Đã tạo job ingest #55");
  });
});
