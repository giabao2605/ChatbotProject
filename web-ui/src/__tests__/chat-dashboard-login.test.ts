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
import type { UserProfile } from "@/types";

const routerState = vi.hoisted(() => ({
  push: vi.fn(),
  query: {} as Record<string, unknown>,
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: routerState.push }),
  useRoute: () => ({ query: routerState.query }),
}));

vi.mock("@/api/client", () => ({
  deleteSession: vi.fn(),
  listSessions: vi.fn(),
  loadDashboard: vi.fn(),
  loadHistory: vi.fn(),
  login: vi.fn(),
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
      currentPartIds: ["P-101"],
      conversationContext: { active: "manual" },
    });
    expect(sessionStorage.getItem("chat-active-session")).toBe(originalSession);

    await button(wrapper, "Hữu ích").trigger("click");
    await flushPromises();
    expect(api.sendFeedback).toHaveBeenCalledWith(42, 1);
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
});
