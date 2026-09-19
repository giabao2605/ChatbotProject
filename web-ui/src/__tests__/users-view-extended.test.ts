import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { defineComponent, nextTick } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/api/client";
import type { ApiRow, RowAction, ToolbarAction } from "@/types";
import UsersView from "@/views/UsersView.vue";

const sampleAccessValue = ["test", "only", "value"].join("-");

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
  },
  template: '<div class="resource-page-stub">{{ title }}</div>',
});

const DialogStub = defineComponent({
  name: "Dialog",
  props: { visible: Boolean, header: String },
  emits: ["update:visible", "hide"],
  template:
    '<section v-if="visible" class="dialog-stub"><h2>{{ header }}</h2><slot /><slot name="footer" /></section>',
});

const ButtonStub = defineComponent({
  name: "Button",
  inheritAttrs: false,
  props: { label: String, disabled: Boolean, loading: Boolean },
  emits: ["click"],
  template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
});

const InputTextStub = defineComponent({
  name: "InputText",
  inheritAttrs: false,
  props: { modelValue: String, type: String },
  emits: ["update:modelValue"],
  template:
    '<input :type="type || \'text\'" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
});

const MessageStub = defineComponent({
  name: "Message",
  template: '<div class="message-stub"><slot /></div>',
});

const global = {
  stubs: {
    ResourcePage: ResourcePageStub,
    Dialog: DialogStub,
    Button: ButtonStub,
    InputText: InputTextStub,
    Message: MessageStub,
  },
};

type PageContract = {
  load: () => Promise<ApiRow[]>;
  rowActions: RowAction[];
  toolbar: ToolbarAction[];
};

function mountUsers(): VueWrapper {
  return mount(UsersView, { global });
}

function page(wrapper: VueWrapper): PageContract {
  return wrapper.findComponent(ResourcePageStub).props() as unknown as PageContract;
}

function button(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll(".dialog-stub button").find((item) => item.text() === label);
  if (!found) throw new Error(`Missing button: ${label}`);
  return found;
}

function action(wrapper: VueWrapper, label: string): RowAction {
  const found = page(wrapper).rowActions.find((item) => item.label === label);
  if (!found) throw new Error(`Missing action: ${label}`);
  return found;
}

function field(wrapper: VueWrapper, label: string) {
  const found = wrapper
    .findAll(".dialog-stub label")
    .find((item) => item.text().includes(label));
  if (!found) throw new Error(`Missing field: ${label}`);
  return found;
}

function checkbox(wrapper: VueWrapper, label: string) {
  const found = wrapper
    .findAll(".dialog-stub .check-row")
    .find((item) => item.text() === label);
  if (!found) throw new Error(`Missing checkbox: ${label}`);
  return found.find('input[type="checkbox"]');
}

async function openCreate(wrapper: VueWrapper) {
  const pending = page(wrapper).toolbar[0].run();
  await nextTick();
  return { pending };
}

async function openEdit(wrapper: VueWrapper, label: string, row: ApiRow) {
  const pending = action(wrapper, label).run(row);
  await flushPromises();
  return { pending };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.apiGet).mockImplementation((path, params) => {
    if (path === "/api/catalog/departments" && params?.active_only === true) {
      return Promise.resolve({
        departments: [
          { code: "ME", name: "Cơ khí" },
          { DeptCode: "QA", DeptName: "Chất lượng" },
          { code: "", name: "Không hợp lệ" },
        ],
      }) as never;
    }
    if (path === "/api/catalog/sites" && params?.active_only === true) {
      return Promise.resolve({
        sites: [
          { code: "HN", name: "Hà Nội" },
          { SiteCode: "HCM", SiteName: "" },
          { code: "", name: "Không hợp lệ" },
        ],
      }) as never;
    }
    if (path === "/api/users") {
      return Promise.resolve({ users: [{ UserID: 7, Username: "bao" }] }) as never;
    }
    const match = /^\/api\/users\/(\d+)$/.exec(path);
    if (match) {
      return Promise.resolve({
        roles: ["viewer", "uploader"],
        departments: ["OLD"],
        sites: ["LEGACY"],
        clearance: "confidential",
      }) as never;
    }
    return Promise.reject(new Error(`Unexpected GET: ${path} ${JSON.stringify(params ?? null)}`)) as never;
  });
  vi.mocked(api.apiSend).mockResolvedValue({ ok: true } as never);
});

describe("UsersView public contracts", () => {
  it("loads users and creates an account with normalized catalog values", async () => {
    const wrapper = mountUsers();
    await flushPromises();

    expect(await page(wrapper).load()).toEqual([{ UserID: 7, Username: "bao" }]);
    expect(api.apiGet).toHaveBeenCalledWith("/api/catalog/departments", { active_only: true });
    expect(api.apiGet).toHaveBeenCalledWith("/api/catalog/sites", { active_only: true });

    const { pending } = await openCreate(wrapper);
    expect(wrapper.text()).toContain("Tạo người dùng");
    expect(wrapper.text()).toContain("ME — Cơ khí");
    expect(wrapper.text()).toContain("QA — Chất lượng");
    expect(wrapper.text()).toContain("HN — Hà Nội");
    expect(wrapper.text()).toContain("HCM");
    expect(wrapper.text()).not.toContain("Không hợp lệ");

    const textInputs = wrapper.findAll('.dialog-stub input[type="text"]');
    await textInputs[0].setValue("  new-user  ");
    await wrapper.find('.dialog-stub input[type="password"]').setValue(sampleAccessValue);
    await textInputs[1].setValue("Người mới");
    await field(wrapper, "Phòng ban chính").find("select").setValue("ME");
    await checkbox(wrapper, "reviewer").setValue(true);
    await checkbox(wrapper, "QA — Chất lượng").setValue(true);
    await checkbox(wrapper, "HN — Hà Nội").setValue(true);
    await field(wrapper, "Mức mật tối đa").find("select").setValue("confidential");
    await button(wrapper, "Lưu").trigger("click");
    await pending;

    expect(api.apiSend).toHaveBeenCalledWith("/api/users", "POST", {
      username: "new-user",
      password: sampleAccessValue,
      display_name: "Người mới",
      department: "ME",
      roles: ["viewer", "reviewer"],
      departments: ["QA"],
      sites: ["HN"],
      max_level: "confidential",
    });
    expect(wrapper.find(".dialog-stub").exists()).toBe(false);
  });

  it("keeps create validation and write failures inside the dialog", async () => {
    const wrapper = mountUsers();
    await flushPromises();
    const { pending } = await openCreate(wrapper);

    await button(wrapper, "Lưu").trigger("click");
    expect(wrapper.text()).toContain("Nhập tên đăng nhập");
    expect(api.apiSend).not.toHaveBeenCalled();

    await wrapper.find('.dialog-stub input[type="text"]').setValue("user");
    await wrapper.find('.dialog-stub input[type="password"]').setValue("short");
    await button(wrapper, "Lưu").trigger("click");
    expect(wrapper.text()).toContain("Mật khẩu tối thiểu 8 ký tự");

    vi.mocked(api.apiSend).mockRejectedValueOnce("opaque failure");
    await wrapper.find('.dialog-stub input[type="password"]').setValue("long-enough");
    await button(wrapper, "Lưu").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Lỗi");
    expect(wrapper.find(".dialog-stub").exists()).toBe(true);

    await button(wrapper, "Huỷ").trigger("click");
    await expect(pending).rejects.toThrow("");
  });

  it("computes role additions and removals from the current server state", async () => {
    const wrapper = mountUsers();
    await flushPromises();
    const { pending } = await openEdit(wrapper, "Đổi vai trò", {
      UserID: 7,
      Username: "bao",
    });

    expect(wrapper.text()).toContain("Đổi vai trò — bao");
    await checkbox(wrapper, "viewer").setValue(false);
    await checkbox(wrapper, "reviewer").setValue(true);
    await button(wrapper, "Lưu").trigger("click");
    await pending;

    expect(api.apiGet).toHaveBeenCalledWith("/api/users/7");
    expect(api.apiSend).toHaveBeenCalledWith("/api/users/7/roles", "PATCH", {
      is_active: true,
      add_roles: ["reviewer"],
      del_roles: ["viewer"],
    });
  });

  it("updates departments and sites while retaining inactive assigned values", async () => {
    const wrapper = mountUsers();
    await flushPromises();

    const { pending: department } = await openEdit(wrapper, "Phòng ban", {
      id: 8,
      username: "linh",
    });
    expect(wrapper.text()).toContain("OLD");
    await checkbox(wrapper, "OLD").setValue(false);
    await checkbox(wrapper, "ME — Cơ khí").setValue(true);
    await button(wrapper, "Lưu").trigger("click");
    await department;
    expect(api.apiSend).toHaveBeenCalledWith("/api/users/8/departments", "PATCH", {
      departments: ["ME"],
    });

    const { pending: site } = await openEdit(wrapper, "Site", {
      LegacyUserIdCode: 9,
      Username: "minh",
    });
    expect(wrapper.text()).toContain("LEGACY");
    await checkbox(wrapper, "HCM").setValue(true);
    await button(wrapper, "Lưu").trigger("click");
    await site;
    expect(api.apiSend).toHaveBeenCalledWith("/api/users/9/sites", "PATCH", {
      sites: ["LEGACY", "HCM"],
    });
  });

  it("updates clearance and validates password changes", async () => {
    const wrapper = mountUsers();
    await flushPromises();

    const { pending: clearance } = await openEdit(wrapper, "Mức mật", {
      UserID: 10,
      Username: "an",
    });
    await wrapper.find(".dialog-stub select").setValue("internal");
    await button(wrapper, "Lưu").trigger("click");
    await clearance;
    expect(api.apiSend).toHaveBeenCalledWith("/api/users/10/clearance", "PATCH", {
      max_level: "internal",
    });

    const { pending: password } = await openEdit(wrapper, "Đổi mật khẩu", {
      UserID: 10,
      Username: "an",
    });
    await wrapper.find('.dialog-stub input[type="password"]').setValue("short");
    await button(wrapper, "Lưu").trigger("click");
    expect(wrapper.text()).toContain("Mật khẩu tối thiểu 8 ký tự");
    await wrapper.find('.dialog-stub input[type="password"]').setValue(sampleAccessValue);
    await button(wrapper, "Lưu").trigger("click");
    await password;
    expect(api.apiSend).toHaveBeenCalledWith("/api/users/10/password", "PATCH", {
      password: sampleAccessValue,
    });
  });

  it("runs activation and permanent deletion against resolved user identifiers", async () => {
    const wrapper = mountUsers();
    await flushPromises();

    await action(wrapper, "Kích hoạt").run({ UserID: 11 });
    await action(wrapper, "Vô hiệu").run({ id: 12 });
    await action(wrapper, "Xoá").run({ external_userid: 13 });

    expect(api.apiSend).toHaveBeenCalledWith("/api/users/11/active", "PATCH", { is_active: true });
    expect(api.apiSend).toHaveBeenCalledWith("/api/users/12/active", "PATCH", { is_active: false });
    expect(api.apiSend).toHaveBeenCalledWith("/api/users/13", "DELETE");
  });

  it("fails closed when catalogs or current permissions cannot be loaded", async () => {
    vi.mocked(api.apiGet).mockRejectedValueOnce(new Error("departments offline"));
    vi.mocked(api.apiGet).mockRejectedValueOnce(new Error("sites offline"));
    const wrapper = mountUsers();
    await flushPromises();

    const { pending: create } = await openCreate(wrapper);
    expect(wrapper.text()).toContain("Chưa có danh mục phòng ban.");
    expect(wrapper.text()).toContain("Chưa có danh mục site.");
    await button(wrapper, "Huỷ").trigger("click");
    await expect(create).rejects.toThrow("");

    vi.mocked(api.apiGet).mockRejectedValueOnce(new Error("permission denied"));
    const { pending: editError } = await openEdit(wrapper, "Đổi vai trò", {
      UserID: 14,
      Username: "blocked",
    });
    expect(wrapper.text()).toContain("permission denied");
    await button(wrapper, "Huỷ").trigger("click");
    await expect(editError).rejects.toThrow("");

    vi.mocked(api.apiGet).mockRejectedValueOnce("opaque");
    const { pending: editOpaque } = await openEdit(wrapper, "Đổi vai trò", {
      UserID: 15,
      Username: "blocked",
    });
    expect(wrapper.text()).toContain("Không tải được quyền hiện tại");
    await button(wrapper, "Huỷ").trigger("click");
    await expect(editOpaque).rejects.toThrow("");
  });

  it("returns an empty list when the users payload omits rows", async () => {
    vi.mocked(api.apiGet).mockImplementation((path) => {
      if (path === "/api/users") return Promise.resolve({}) as never;
      if (path === "/api/catalog/departments") return Promise.resolve({}) as never;
      if (path === "/api/catalog/sites") return Promise.resolve({}) as never;
      return Promise.reject(new Error(`Unexpected GET: ${path}`)) as never;
    });
    const wrapper = mountUsers();
    await flushPromises();
    expect(await page(wrapper).load()).toEqual([]);
  });
});
