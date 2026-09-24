import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/api/client";
import { setLocale } from "@/i18n";
import LoginView from "@/views/LoginView.vue";
import HelpView from "@/views/HelpView.vue";

vi.mock("@/api/client", () => ({ login: vi.fn() }));
vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn() }),
}));

const card = { template: "<article><slot name='title' /><slot name='subtitle' /><slot name='content' /></article>" };
const passwordField = {
  name: "Password",
  props: ["modelValue", "promptLabel"],
  emits: ["update:modelValue"],
  template: "<input :value='modelValue' @input='$emit(`update:modelValue`, $event.target.value)' />",
};
const inputField = { ...passwordField, name: "InputText" };
const stubs = {
  Card: card,
  InputText: inputField,
  Password: passwordField,
  Button: { props: ["label"], template: "<button>{{ label }}</button>" },
  Message: { template: "<div><slot /></div>" },
};

beforeEach(() => {
  vi.clearAllMocks();
  setActivePinia(createPinia());
  setLocale("vi");
});

describe("login language", () => {
  it("gives the password control a Vietnamese prompt", () => {
    const wrapper = mount(LoginView, { global: { stubs } });
    expect(wrapper.findComponent({ name: "Password" }).props("promptLabel")).toBe("Nhập mật khẩu");
  });

  it("validates empty fields in Vietnamese before calling the API", async () => {
    const wrapper = mount(LoginView, { global: { stubs } });
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(api.login).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("Vui lòng nhập tên đăng nhập và mật khẩu");
    expect(wrapper.text()).not.toContain("String should have at least 1 character");
  });
});

describe("help language", () => {
  it("translates all six sections when switching between English and Vietnamese", async () => {
    const wrapper = mount(HelpView, { global: { stubs: { Card: card } } });
    expect(wrapper.text()).toContain("1. Chat & trích dẫn");

    setLocale("en");
    await flushPromises();
    expect(wrapper.findAll("h3")).toHaveLength(6);
    expect(wrapper.text()).toContain("1. Chat & citations");
    expect(wrapper.text()).toContain("Ask a question on the Chat page");
    expect(wrapper.text()).toContain("6. Language");
    expect(wrapper.text()).not.toContain("Đặt câu hỏi ở trang Chat");

    setLocale("vi");
    await flushPromises();
    expect(wrapper.text()).toContain("Đặt câu hỏi ở trang Chat");
  });
});
