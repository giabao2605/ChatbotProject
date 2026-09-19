import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { defineComponent } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "@/api/client";
import type { ApiRow, ResourceColumn, RowAction } from "@/types";
import ReviewView from "@/views/ReviewView.vue";

const router = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("vue-router", () => ({
  useRouter: () => router,
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
  },
  template: '<div class="resource-page-stub"></div>',
});

const ButtonStub = defineComponent({
  name: "Button",
  props: {
    label: String,
    disabled: Boolean,
    loading: Boolean,
  },
  emits: ["click"],
  template:
    '<button type="button" :disabled="disabled" :data-loading="String(loading)" @click="$emit(\'click\')">{{ label }}</button>',
});

const InputTextStub = defineComponent({
  name: "InputText",
  props: { modelValue: [String, Number] },
  emits: ["update:modelValue"],
  template:
    '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
});

const DialogStub = defineComponent({
  name: "Dialog",
  props: { visible: Boolean, header: String },
  emits: ["update:visible"],
  template:
    '<div v-if="visible" class="dialog-stub"><h2>{{ header }}</h2><slot /><slot name="footer" /></div>',
});

const stubs = {
  ResourcePage: ResourcePageStub,
  Button: ButtonStub,
  InputText: InputTextStub,
  Dialog: DialogStub,
  Message: { template: '<div class="message"><slot /></div>' },
  Card: { template: '<article><slot name="content" /></article>' },
};

type ReviewContract = {
  load: () => Promise<ApiRow[]>;
  columns: ResourceColumn[];
  rowActions: RowAction[];
};

let bulkJobs: ApiRow[];
let metadataDocs: ApiRow[];
let departments: string[];
let pendingDocuments: ApiRow[];
let contractResponse: Record<string, unknown>;

function getResponse(path: string): unknown {
  if (path === "/api/ingestion/bulk-action-jobs") return { jobs: bulkJobs };
  if (path === "/api/documents/bulk-meta") {
    return { documents: metadataDocs, departments };
  }
  if (path === "/api/documents/pending-review") {
    return { documents: pendingDocuments };
  }
  if (path.endsWith("/publish-contract")) return contractResponse;
  throw new Error(`Unexpected GET ${path}`);
}

async function mountReview(): Promise<VueWrapper> {
  const wrapper = mount(ReviewView as never, { global: { stubs } });
  await flushPromises();
  return wrapper;
}

function contract(wrapper: VueWrapper): ReviewContract {
  return wrapper.findComponent(ResourcePageStub).props() as unknown as ReviewContract;
}

function button(wrapper: VueWrapper, label: string, occurrence = 0) {
  const matches = wrapper.findAll("button").filter((item) => item.text() === label);
  expect(matches.length).toBeGreaterThan(occurrence);
  return matches[occurrence];
}

function field(wrapper: VueWrapper, labelText: string, selector: string) {
  const label = wrapper
    .findAll("label")
    .find((item) => item.text().includes(labelText));
  expect(label, `field "${labelText}"`).toBeDefined();
  const control = label!.find(selector);
  expect(control.exists(), `${selector} in "${labelText}"`).toBe(true);
  return control;
}

function action(wrapper: VueWrapper, label: string): RowAction {
  const found = contract(wrapper).rowActions.find((item) => item.label === label);
  expect(found, `action "${label}"`).toBeDefined();
  return found!;
}

beforeEach(() => {
  vi.clearAllMocks();
  bulkJobs = [];
  metadataDocs = [];
  departments = [];
  pendingDocuments = [];
  contractResponse = { valid: true, ok: true, issues: [] };
  vi.mocked(api.apiGet).mockImplementation(
    async (path: string) => getResponse(path) as never,
  );
  vi.mocked(api.apiSend).mockResolvedValue({ ok: true } as never);
});

describe("ReviewView public behavior", () => {
  it("loads the review queues and presents normalized extraction evidence", async () => {
    bulkJobs = [{ JobID: 1 }];
    metadataDocs = [{ DocID: 10 }];
    departments = ["ME"];
    pendingDocuments = [
      {
        JobID: 1,
        ExtractionReport: {
          status: "complete",
          total_pages: 3,
          total_chunks: 8,
          pages_table_extracted: [1, 2],
          failed_pages: [3],
          quality_score: 0.91,
          time_taken: 1.25,
          quality_policy_version: "quality-v2",
          quality_reasons: ["ocr_ok", { code: "table_ok" }, ""],
          quality_components: { ocr: 0.9, layout: "pass" },
        },
      },
      {
        JobID: 2,
        ExtractionReport: JSON.stringify({
          policy_version: "quality-v1",
          reason_codes: ["legacy_reason"],
          score_components: { text: 1 },
        }),
      },
      { JobID: 3, ExtractionReport: "{bad json" },
      { JobID: 4, ExtractionReport: JSON.stringify([]) },
      { JobID: 5, ExtractionReport: 17 },
      {
        JobID: 6,
        ExtractionReport: {
          reasons: "not-an-array",
          score_components: [],
        },
      },
      { JobID: 7 },
    ];

    const wrapper = await mountReview();
    expect(api.apiGet).toHaveBeenCalledWith("/api/ingestion/bulk-action-jobs");
    expect(api.apiGet).toHaveBeenCalledWith("/api/documents/bulk-meta", {
      dept: "",
      domain: "",
    });

    const page = contract(wrapper);
    expect(page.columns.map((column) => column.field)).toEqual([
      "JobID",
      "TenFile",
      "ThuMuc",
      "UploadedBy",
      "UpdatedAt",
      "ExtractionSummary",
      "QualityDetails",
      "Domain",
      "SecurityLevel",
      "Site",
    ]);
    const rows = await page.load();

    expect(api.apiGet).toHaveBeenCalledWith("/api/documents/pending-review");
    expect(rows[0]).toMatchObject({
      ExtractionSummary:
        "complete, 3 trang, 8 chunks, 2 bảng, 1 trang lỗi, chất lượng 0.91, 1.3s",
      QualityDetails:
        'Chính sách quality-v2; ocr: 0.9; layout: pass; ocr_ok; {"code":"table_ok"}',
    });
    expect(rows[1]).toMatchObject({
      ExtractionSummary: "unknown, 0 trang, 0 chunks",
      QualityDetails: "Chính sách quality-v1; text: 1; legacy_reason",
    });
    for (const row of rows.slice(2, 5)) {
      expect(row.ExtractionSummary).toBe("Chưa có báo cáo ingest");
      expect(row.QualityDetails).toBe("Chưa có dữ liệu giải thích điểm.");
    }
    expect(rows[5].QualityDetails).toBe("Chính sách chưa ghi phiên bản");
    expect(rows[6].ExtractionSummary).toBe("Chưa có báo cáo ingest");
  });

  it("publishes every supported document mode only after contract validation", async () => {
    const wrapper = await mountReview();
    const row = { DocID: 11, JobID: 22 };

    await action(wrapper, "Xuất bản (version)").run(row);
    await action(wrapper, "Xuất bản (variant)").run(row);
    await action(wrapper, "Xuất bản (độc lập)").run(row);

    expect(api.apiGet).toHaveBeenCalledTimes(5);
    expect(api.apiGet).toHaveBeenCalledWith("/api/documents/11/publish-contract");
    expect(api.apiSend).toHaveBeenCalledTimes(6);
    expect(api.apiSend).toHaveBeenNthCalledWith(
      1,
      "/api/documents/11/publish-new-version",
      "POST",
      undefined,
    );
    expect(api.apiSend).toHaveBeenNthCalledWith(
      2,
      "/api/ingestion/jobs/22/publish",
      "POST",
      undefined,
    );
    expect(api.apiSend).toHaveBeenNthCalledWith(
      3,
      "/api/documents/11/publish-new-variant",
      "POST",
      undefined,
    );
    expect(api.apiSend).toHaveBeenNthCalledWith(
      5,
      "/api/documents/11/publish-standalone",
      "POST",
      undefined,
    );
  });

  it("fails closed for missing documents and invalid publish contracts", async () => {
    const wrapper = await mountReview();
    const publish = action(wrapper, "Xuất bản (version)");

    await expect(publish.run({ JobID: 9 })).rejects.toThrow(
      "Chưa tạo được tài liệu nên không thể xuất bản.",
    );
    expect(api.apiSend).not.toHaveBeenCalled();

    contractResponse = {
      valid: true,
      ok: true,
      issues: [
        { field: "title", code: "required", message: "Thiếu tiêu đề" },
        { code: "invalid_owner" },
        {},
      ],
    };
    await expect(publish.run({ DocID: 12, JobID: 9 })).rejects.toThrow(
      "Không thể publish. [title] Thiếu tiêu đề; invalid_owner; Metadata chưa hợp lệ",
    );
    await flushPromises();
    expect(wrapper.find(".dialog-stub").text()).toContain("Thiếu tiêu đề");
    expect(wrapper.find(".dialog-stub").text()).toContain("Metadata");
    expect(wrapper.find(".dialog-stub").text()).toContain("invalid");

    await button(wrapper, "Mở kho tài liệu để sửa metadata").trigger("click");
    expect(router.push).toHaveBeenCalledWith({
      path: "/documents",
      query: { tab: "effective", doc: 12 },
    });
    expect(wrapper.find(".dialog-stub").exists()).toBe(false);

    contractResponse = { valid: false, ok: true };
    await expect(publish.run({ DocID: 13, JobID: 9 })).rejects.toThrow(
      "Tài liệu chưa đáp ứng publish contract.",
    );
    await button(wrapper, "Đóng").trigger("click");
    expect(wrapper.find(".dialog-stub").exists()).toBe(false);

    contractResponse = { valid: true, ok: false, issues: [] };
    await expect(publish.run({ DocID: 14, JobID: 9 })).rejects.toThrow(
      "Tài liệu chưa đáp ứng publish contract.",
    );
    expect(api.apiSend).not.toHaveBeenCalled();
    wrapper.findComponent(DialogStub).vm.$emit("update:visible", false);
    await flushPromises();
    expect(wrapper.find(".dialog-stub").exists()).toBe(false);
  });

  it("runs reject, archive, and delete steps with exact methods and payloads", async () => {
    const wrapper = await mountReview();
    const row = { doc_id: 31, job_id: 41 };

    await action(wrapper, "Từ chối").run(row);
    await action(wrapper, "Lưu trữ").run(row);
    await action(wrapper, "Xoá").run(row);
    await action(wrapper, "Từ chối").run({ JobID: 42 });

    expect(api.apiSend).toHaveBeenNthCalledWith(
      1,
      "/api/documents/31/reject",
      "POST",
      undefined,
    );
    expect(api.apiSend).toHaveBeenNthCalledWith(
      2,
      "/api/ingestion/jobs/41/reject",
      "POST",
      { reason: "Từ chối từ trang duyệt tài liệu" },
    );
    expect(api.apiSend).toHaveBeenNthCalledWith(
      3,
      "/api/documents/31/archive",
      "POST",
      undefined,
    );
    expect(api.apiSend).toHaveBeenNthCalledWith(
      5,
      "/api/documents/31",
      "DELETE",
      undefined,
    );
    expect(api.apiSend).toHaveBeenNthCalledWith(
      6,
      "/api/ingestion/jobs/41",
      "DELETE",
      undefined,
    );
    expect(api.apiSend).toHaveBeenLastCalledWith(
      "/api/ingestion/jobs/42/reject",
      "POST",
      { reason: "Từ chối từ trang duyệt tài liệu" },
    );

    await expect(action(wrapper, "Lưu trữ").run({ DocID: 50 })).rejects.toThrow(
      "Không tìm thấy JobID của dòng này.",
    );
    expect(api.apiSend).toHaveBeenLastCalledWith(
      "/api/documents/50/archive",
      "POST",
      undefined,
    );
  });

  it("surfaces structured document-action failures before touching the job", async () => {
    const wrapper = await mountReview();
    const reject = action(wrapper, "Từ chối");
    const row = { DocID: 3, JobID: 4 };
    const send = vi.mocked(api.apiSend);

    send.mockResolvedValueOnce({
      ok: false,
      validation: { issues: [{ message: "Sai title" }, {}, { message: "Sai owner" }] },
    } as never);
    await expect(reject.run(row)).rejects.toThrow("Sai title; Sai owner");
    expect(send).toHaveBeenCalledOnce();

    send.mockResolvedValueOnce({ ok: false, error: "Không có quyền" } as never);
    await expect(reject.run(row)).rejects.toThrow("Không có quyền");

    send.mockResolvedValueOnce({ ok: false } as never);
    await expect(reject.run(row)).rejects.toThrow("Thao tác thất bại.");

    send.mockResolvedValueOnce({ result: "legacy-success" } as never);
    send.mockResolvedValueOnce({ ok: true } as never);
    await expect(reject.run(row)).resolves.toBeUndefined();
  });

  it("validates bulk selection and reports mixed publish results", async () => {
    bulkJobs = [
      { JobID: 1, DocID: 10, Status: "ready", TenFile: "a.pdf" },
      { job_id: 2, Status: "ready", TenFile: "b.pdf" },
      { JobID: 0, Status: "invalid", TenFile: "bad.pdf" },
    ];
    const wrapper = await mountReview();

    await button(wrapper, "Publish đã chọn").trigger("click");
    expect(wrapper.text()).toContain("Chọn ít nhất một job.");
    expect(api.apiSend).not.toHaveBeenCalled();

    await field(wrapper, "a.pdf", 'input[type="checkbox"]').setValue(true);
    await field(wrapper, "b.pdf", 'input[type="checkbox"]').setValue(true);
    await field(wrapper, "Kiểu publish", "select").setValue("new_version");
    await field(wrapper, "Lý do reject", "input").setValue("duplicate");
    vi.mocked(api.apiSend).mockResolvedValueOnce({
      updated: 1,
      failed: 3,
      failures: [
        { validation: { issues: [{ message: "Thiếu title" }, {}] } },
        { error: "Sai phiên bản" },
        {},
      ],
    } as never);

    await button(wrapper, "Publish đã chọn").trigger("click");
    await flushPromises();

    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/documents/review/bulk",
      "POST",
      {
        items: [
          { job_id: 1, doc_id: 10 },
          { job_id: 2, doc_id: null },
        ],
        action: "publish",
        publish_mode: "new_version",
        reason: "duplicate",
      },
    );
    expect(wrapper.text()).toContain("Hoàn tất: 1 thành công, 3 thất bại");
    expect(wrapper.text()).toContain(
      "Thiếu title; Sai phiên bản; Publish thất bại",
    );
    expect(api.apiGet).toHaveBeenCalledWith("/api/ingestion/bulk-action-jobs");
  });

  it("recovers bulk actions from Error and opaque failures and prunes stale selection", async () => {
    bulkJobs = [{ JobID: 5, DocID: 15, Status: "ready", TenFile: "five.pdf" }];
    const wrapper = await mountReview();
    await field(wrapper, "five.pdf", 'input[type="checkbox"]').setValue(true);

    vi.mocked(api.apiSend).mockRejectedValueOnce(new Error("bulk offline"));
    await button(wrapper, "Reject đã chọn").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("bulk offline");

    vi.mocked(api.apiSend).mockRejectedValueOnce("opaque");
    await button(wrapper, "Xóa đã chọn").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Lỗi");

    vi.mocked(api.apiSend).mockResolvedValueOnce({
      updated: 1,
      failed: 0,
    } as never);
    await button(wrapper, "Reject đã chọn").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Hoàn tất: 1 thành công, 0 thất bại");

    bulkJobs = [{ JobID: 6, Status: "ready", TenFile: "six.pdf" }];
    await button(wrapper, "Tải lại", 0).trigger("click");
    await flushPromises();
    await button(wrapper, "Publish đã chọn").trigger("click");
    expect(wrapper.text()).toContain("Chọn ít nhất một job.");
  });

  it("validates, filters, and applies trimmed bulk metadata", async () => {
    metadataDocs = [
      { DocID: 20, TenFile: "pump.pdf", ThuMuc: "ME" },
      { doc_id: 21, OriginalFileName: "valve.pdf", Department: "QA" },
    ];
    departments = ["ME", "QA"];
    const wrapper = await mountReview();

    await button(wrapper, "Áp dụng metadata").trigger("click");
    expect(wrapper.text()).toContain("Chọn ít nhất một tài liệu.");

    await field(wrapper, "pump.pdf", 'input[type="checkbox"]').setValue(true);
    await button(wrapper, "Áp dụng metadata").trigger("click");
    expect(wrapper.text()).toContain("Nhập ít nhất một metadata để cập nhật.");

    await field(wrapper, "Tiêu đề", "input").setValue("  Pump manual  ");
    await field(wrapper, "Tags", "input").setValue("  ");
    await field(wrapper, "Người ký / chủ quản", "input").setValue("  QA lead  ");
    await field(wrapper, "Ngôn ngữ", "input").setValue("  vi  ");
    await field(wrapper, "Trạng thái hiệu lực", "input").setValue("  active  ");
    await field(wrapper, "Tóm tắt", "textarea").setValue("  Service steps  ");
    await field(wrapper, "Domain", "select").setValue("mechanical");
    vi.mocked(api.apiSend).mockResolvedValueOnce({
      updated: 1,
      failed: 0,
    } as never);

    await button(wrapper, "Áp dụng metadata").trigger("click");
    await flushPromises();
    expect(api.apiSend).toHaveBeenCalledWith(
      "/api/documents/bulk-metadata",
      "PATCH",
      {
        doc_ids: [20],
        metadata: {
          title: "Pump manual",
          summary: "Service steps",
          owner_signer: "QA lead",
          effective_status: "active",
          language: "vi",
          domain: "mechanical",
        },
      },
    );
    expect(wrapper.text()).toContain("Đã cập nhật: 1 thành công, 0 thất bại");

    await field(wrapper, "Phòng ban", "select").setValue("QA");
    await field(wrapper, "Domain", "select").setValue("tabular");
    await flushPromises();
    expect(api.apiGet).toHaveBeenCalledWith("/api/documents/bulk-meta", {
      dept: "QA",
      domain: "tabular",
    });
  });

  it("reports metadata update failures and drops removed document selections", async () => {
    metadataDocs = [{ DocID: 30, TenFile: "motor.pdf", ThuMuc: "ME" }];
    const wrapper = await mountReview();
    await field(wrapper, "motor.pdf", 'input[type="checkbox"]').setValue(true);
    await field(wrapper, "Số hiệu", "input").setValue("M-30");

    vi.mocked(api.apiSend).mockRejectedValueOnce(new Error("metadata offline"));
    await button(wrapper, "Áp dụng metadata").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("metadata offline");

    vi.mocked(api.apiSend).mockRejectedValueOnce("opaque");
    await button(wrapper, "Áp dụng metadata").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Lỗi");

    metadataDocs = [{ DocID: 31, TenFile: "new.pdf", ThuMuc: "ME" }];
    await button(wrapper, "Tải lại", 1).trigger("click");
    await flushPromises();
    await button(wrapper, "Áp dụng metadata").trigger("click");
    expect(wrapper.text()).toContain("Chọn ít nhất một tài liệu.");
  });

  it("handles list endpoints that omit optional arrays", async () => {
    vi.mocked(api.apiGet).mockImplementation(async (path: string) => {
      if (path === "/api/ingestion/bulk-action-jobs") return {} as never;
      if (path === "/api/documents/bulk-meta") return {} as never;
      if (path === "/api/documents/pending-review") return {} as never;
      return { valid: true } as never;
    });
    const wrapper = await mountReview();

    expect(wrapper.text()).toContain("Không có job nào đủ điều kiện.");
    expect(wrapper.text()).toContain("Không có tài liệu nào.");
    await expect(contract(wrapper).load()).resolves.toEqual([]);
  });
});
