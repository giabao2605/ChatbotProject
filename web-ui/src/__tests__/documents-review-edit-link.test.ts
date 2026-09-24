import { flushPromises, mount } from "@vue/test-utils";
import { defineComponent } from "vue";
import { beforeEach, expect, it, vi } from "vitest";
import * as api from "@/api/client";
import DocumentsView from "@/views/DocumentsView.vue";

const state = vi.hoisted(() => ({
  route: { query: { doc: "144" } as Record<string, unknown> },
  roles: ["reviewer"],
}));

vi.mock("vue-router", () => ({
  useRoute: () => state.route,
  useRouter: () => ({ replace: vi.fn() }),
}));
vi.mock("@/stores/auth", () => ({
  useAuthStore: () => ({ user: { roles: state.roles } }),
}));
vi.mock("@/api/client", () => ({ apiGet: vi.fn(), apiSend: vi.fn() }));

const stubs = {
  ResourcePage: { template: "<div />" },
  LifecycleView: { template: "<div />" },
  Dialog: defineComponent({
    props: { visible: Boolean },
    template: '<div v-if="visible" class="dialog-stub"><slot /><slot name="footer" /></div>',
  }),
  Button: defineComponent({
    props: { label: String, type: String },
    emits: ["click"],
    template: '<button :type="type || \'button\'" @click="$emit(\'click\')">{{ label }}</button>',
  }),
  InputText: defineComponent({
    props: { modelValue: String },
    emits: ["update:modelValue"],
    template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  }),
  Message: { template: "<div class='message'><slot /></div>" },
};

beforeEach(() => {
  vi.clearAllMocks();
  state.route.query = { doc: "144" };
  state.roles = ["reviewer"];
  vi.mocked(api.apiGet).mockResolvedValue({
    documents: [{ DocID: 144, Title: "Bản cũ", Summary: "Tóm tắt", ReviewStatus: "pending_review" }],
  } as never);
  vi.mocked(api.apiSend).mockResolvedValue({ ok: true } as never);
});

it("opens the pending review document metadata form from a review link and saves that document", async () => {
  const wrapper = mount(DocumentsView, { global: { stubs } });
  await flushPromises();

  expect(api.apiGet).toHaveBeenCalledWith("/api/documents/pending-review");
  const dialog = wrapper.find(".dialog-stub");
  expect(dialog.exists()).toBe(true);
  const title = dialog.findAll("label").find((item) => item.text().includes("Tiêu đề"));
  expect(title?.find("input").element.value).toBe("Bản cũ");
  await title?.find("input").setValue("Bản mới");
  await dialog.find("form").trigger("submit");
  await flushPromises();
  expect(api.apiSend).toHaveBeenCalledWith(
    "/api/documents/144/metadata", "PATCH", expect.objectContaining({ title: "Bản mới" }),
  );
});

it("does not open a pending document that is absent from the authorized review queue", async () => {
  vi.mocked(api.apiGet).mockResolvedValue({ documents: [{ DocID: 145, Title: "Khác" }] } as never);
  const wrapper = mount(DocumentsView, { global: { stubs } });
  await flushPromises();
  expect(wrapper.find(".dialog-stub").exists()).toBe(false);
  expect(wrapper.text()).toContain("Không tìm thấy tài liệu chờ duyệt");
  expect(api.apiSend).not.toHaveBeenCalled();
});

it("does not read the review queue for users without management rights", async () => {
  state.roles = ["viewer"];
  const wrapper = mount(DocumentsView, { global: { stubs } });
  await flushPromises();
  expect(api.apiGet).not.toHaveBeenCalled();
  expect(wrapper.find(".dialog-stub").exists()).toBe(false);
});
