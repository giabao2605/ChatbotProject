<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { apiGet, apiUpload } from "@/api/client";
import { t } from "@/i18n";
import { SECURITY_LEVELS } from "@/utils/rows";
import type { ApiRow } from "@/types";

const router = useRouter();
const files = ref<File[]>([]);
const busy = ref(false);
const error = ref("");
const notice = ref("");

const departments = ref<Array<{ code: string; name: string }>>([]);
const sites = ref<Array<{ code: string; name: string }>>([]);
const catalogError = ref("");

const LANGUAGES = ["vi", "en", "ja", "zh", "ko"];
const EFFECTIVE_STATUSES = ["draft", "effective", "superseded", "expired", "withdrawn"];

const uploadMode = ref<"batch" | "per_file">("batch");
const batchExtraDepartments = ref<string[]>([]);
const assignments = reactive<Record<number, { thu_muc: string; extra_departments: string[] }>>({});

const meta = reactive({
  thu_muc: "",
  domain: "",
  security_level: "",
  cong_doan: "",
  site: "",
});

const detail = reactive<Record<string, string>>({
  title: "",
  summary: "",
  tags: "",
  doc_number: "",
  issued_date: "",
  effective_date: "",
  expiry_date: "",
  review_date: "",
  owner_signer: "",
  language: "",
  effective_status: "",
});

const departmentOptions = computed(() => departments.value.map((d) => d.code));
const selectedFileSummary = computed(() => {
  if (!files.value.length) return "Chưa chọn file";
  const total = files.value.reduce((sum, file) => sum + file.size, 0);
  return `${files.value.length} file, ${formatBytes(total)}`;
});

function pick(row: ApiRow, keys: string[]): string {
  for (const k of keys) {
    const found = Object.keys(row).find((rk) => rk.toLowerCase() === k.toLowerCase());
    if (found && row[found] != null && String(row[found]).trim()) return String(row[found]);
  }
  return "";
}

function formatBytes(value: number): string {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

async function loadCatalog() {
  catalogError.value = "";
  try {
    const [deptRes, siteRes] = await Promise.all([
      apiGet<{ departments: ApiRow[] }>("/api/catalog/departments", { active_only: true }),
      apiGet<{ sites: ApiRow[] }>("/api/catalog/sites", { active_only: true }),
    ]);
    departments.value = (deptRes.departments ?? [])
      .map((row) => ({ code: pick(row, ["code", "DeptCode"]), name: pick(row, ["name", "DeptName"]) }))
      .filter((d) => d.code);
    sites.value = (siteRes.sites ?? [])
      .map((row) => ({ code: pick(row, ["code", "SiteCode"]), name: pick(row, ["name", "SiteName"]) }))
      .filter((s) => s.code);
  } catch (err) {
    catalogError.value = err instanceof Error ? err.message : t("common.error");
  }
}
onMounted(loadCatalog);

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  files.value = input.files ? Array.from(input.files) : [];
  for (const key of Object.keys(assignments)) delete assignments[Number(key)];
  files.value.forEach((_file, index) => {
    assignments[index] = { thu_muc: meta.thu_muc || departmentOptions.value[0] || "", extra_departments: [] };
  });
}

function selectedDept(index: number): string {
  return assignments[index]?.thu_muc || "";
}

function extraOptions(primary: string): string[] {
  return departmentOptions.value.filter((d) => d !== primary);
}

function appendOptional(form: FormData, key: string, value: string) {
  const trimmed = value.trim();
  if (trimmed) form.append(key, trimmed);
}

function buildMetaJson(): Record<string, string> {
  const metaJson: Record<string, string> = {};
  for (const [key, value] of Object.entries(detail)) {
    const trimmed = String(value ?? "").trim();
    if (trimmed) metaJson[key] = trimmed;
  }
  return metaJson;
}

function validate(): boolean {
  error.value = "";
  notice.value = "";
  if (!files.value.length) {
    error.value = t("upload.noFile");
    return false;
  }
  if (uploadMode.value === "batch" && !meta.thu_muc.trim()) {
    error.value = t("upload.noDept");
    return false;
  }
  if (uploadMode.value === "per_file") {
    const missing = files.value.some((_file, index) => !selectedDept(index).trim());
    if (missing) {
      error.value = "Vui lòng chọn phòng ban cho từng file";
      return false;
    }
  }
  return true;
}

async function submit() {
  if (!validate()) return;
  busy.value = true;
  try {
    const form = new FormData();
    const metaJson = buildMetaJson();
    if (Object.keys(metaJson).length) form.append("meta_json", JSON.stringify(metaJson));

    if (files.value.length === 1 && uploadMode.value === "batch") {
      form.append("file", files.value[0]);
      form.append("thu_muc", meta.thu_muc.trim());
      appendOptional(form, "domain", meta.domain);
      appendOptional(form, "security_level", meta.security_level);
      appendOptional(form, "cong_doan", meta.cong_doan);
      appendOptional(form, "site", meta.site);
      if (batchExtraDepartments.value.length) {
        form.append("extra_departments_json", JSON.stringify(batchExtraDepartments.value));
      }
      const result = await apiUpload<{ job_id: number }>("/api/documents/upload", form);
      notice.value = t("upload.success", { id: String(result.job_id) });
    } else {
      files.value.forEach((file) => form.append("files", file));
      appendOptional(form, "domain", meta.domain);
      appendOptional(form, "security_level", meta.security_level);
      appendOptional(form, "cong_doan", meta.cong_doan);
      appendOptional(form, "site", meta.site);
      if (uploadMode.value === "batch") {
        form.append("thu_muc", meta.thu_muc.trim());
        if (batchExtraDepartments.value.length) {
          form.append("extra_departments_json", JSON.stringify(batchExtraDepartments.value));
        }
      } else {
        form.append(
          "assignments_json",
          JSON.stringify(
            files.value.map((_file, index) => ({
              thu_muc: assignments[index]?.thu_muc || "",
              extra_departments: assignments[index]?.extra_departments || [],
            })),
          ),
        );
      }
      const result = await apiUpload<{ created: number; failed: number; errors?: Array<{ file_name: string; error: string }> }>(
        "/api/documents/upload-batch",
        form,
      );
      notice.value = `Đã tạo ${result.created} job ingest${result.failed ? `, lỗi ${result.failed}` : ""}`;
      if (result.failed && result.errors?.length) {
        error.value = result.errors.map((e) => `${e.file_name}: ${e.error}`).join("\n");
      }
    }
    if (!error.value) setTimeout(() => router.push("/queue"), 800);
  } catch (err) {
    error.value = err instanceof Error ? err.message : t("common.error");
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="content-page">
    <header class="page-header">
      <div>
        <div class="eyebrow">Ingestion</div>
        <h1 v-text="t('upload.title')"></h1>
        <p class="page-subtitle" v-text="t('upload.subtitle')"></p>
      </div>
      <div class="page-actions">
        <Button label="Tiến trình ingest" severity="secondary" outlined @click="router.push('/queue')" />
      </div>
    </header>

    <Message v-if="error" severity="error" style="white-space: pre-line" v-text="error"></Message>
    <Message v-if="notice" severity="success" v-text="notice"></Message>
    <Message v-if="catalogError" severity="warn" v-text="catalogError"></Message>

    <Card>
      <template #content>
        <form class="upload-layout" @submit.prevent="submit">
          <section class="upload-section upload-drop">
            <div>
              <h2>Tệp cần ingest</h2>
              <p class="muted-text">Hỗ trợ tài liệu văn phòng, PDF và ảnh scan. Có thể chọn nhiều file một lần.</p>
            </div>
            <label class="file-drop">
              <input
                type="file"
                multiple
                accept=".pdf,.docx,.doc,.xlsx,.xls,.txt,.md,.csv,.pptx,.png,.jpg,.jpeg,.bmp,.gif,.webp,.tif,.tiff"
                @change="onFileChange"
              />
              <span>Chọn file</span>
              <strong v-text="selectedFileSummary"></strong>
            </label>
            <div v-if="files.length" class="file-list">
              <div v-for="(fileItem, index) in files" :key="`${fileItem.name}-${index}`" class="file-row">
                <span v-text="fileItem.name"></span>
                <small v-text="formatBytes(fileItem.size)"></small>
              </div>
            </div>
          </section>

          <section class="upload-section">
            <div class="section-heading">
              <div>
                <h2>Phân quyền và phòng ban</h2>
                <p class="muted-text">Chọn nơi sở hữu tài liệu và các phòng ban được chia sẻ thêm.</p>
              </div>
              <div class="mode-switch">
                <label>
                  <input v-model="uploadMode" type="radio" value="batch" />
                  <span>Cả lô</span>
                </label>
                <label>
                  <input v-model="uploadMode" type="radio" value="per_file" />
                  <span>Từng file</span>
                </label>
              </div>
            </div>

            <div v-if="uploadMode === 'batch'" class="upload-grid">
              <label class="stack-field">
                <span v-text="t('upload.dept')"></span>
                <select v-if="departments.length" v-model="meta.thu_muc" class="native-select">
                  <option value="" disabled>Chọn phòng ban</option>
                  <option
                    v-for="dept in departments"
                    :key="dept.code"
                    :value="dept.code"
                    v-text="dept.name ? `${dept.code} - ${dept.name}` : dept.code"
                  ></option>
                </select>
                <InputText v-else v-model="meta.thu_muc" placeholder="VD: Production" />
              </label>
              <div v-if="departments.length" class="stack-field">
                <span>Chia sẻ thêm</span>
                <div class="checkbox-grid">
                  <label v-for="dept in extraOptions(meta.thu_muc)" :key="dept" class="check-row">
                    <input v-model="batchExtraDepartments" type="checkbox" :value="dept" />
                    <span v-text="dept"></span>
                  </label>
                </div>
              </div>
            </div>

            <div v-else-if="files.length" class="per-file-list">
              <div v-for="(fileItem, index) in files" :key="`${fileItem.name}-${index}`" class="per-file-row">
                <strong v-text="fileItem.name"></strong>
                <label class="stack-field">
                  <span>Phòng ban chính</span>
                  <select v-if="departments.length" v-model="assignments[index].thu_muc" class="native-select">
                    <option value="" disabled>Chọn phòng ban</option>
                    <option
                      v-for="dept in departments"
                      :key="dept.code"
                      :value="dept.code"
                      v-text="dept.name ? `${dept.code} - ${dept.name}` : dept.code"
                    ></option>
                  </select>
                  <InputText v-else v-model="assignments[index].thu_muc" />
                </label>
                <div v-if="departments.length" class="stack-field full-span">
                  <span>Chia sẻ thêm</span>
                  <div class="checkbox-grid">
                    <label v-for="dept in extraOptions(assignments[index].thu_muc)" :key="dept" class="check-row">
                      <input v-model="assignments[index].extra_departments" type="checkbox" :value="dept" />
                      <span v-text="dept"></span>
                    </label>
                  </div>
                </div>
              </div>
            </div>
            <p v-else-if="uploadMode === 'per_file'" class="muted-text">Chọn file trước để gán phòng ban riêng.</p>
          </section>

          <section class="upload-section">
            <h2>Metadata chính</h2>
            <div class="upload-grid">
              <label class="stack-field">
                <span v-text="t('upload.domain')"></span>
                <InputText v-model="meta.domain" placeholder="Để trống = tự suy theo phòng ban" />
              </label>
              <label class="stack-field">
                <span v-text="t('upload.security')"></span>
                <select v-model="meta.security_level" class="native-select">
                  <option value="">Tự động theo phòng ban và nội dung</option>
                  <option v-for="opt in SECURITY_LEVELS" :key="opt.value" :value="opt.value" v-text="opt.label"></option>
                </select>
                <small class="field-help">Để tự động nếu muốn worker tự gán mức mật, rồi nâng lên confidential khi phát hiện dữ liệu nhạy cảm.</small>
              </label>
              <label class="stack-field">
                <span v-text="t('upload.stage')"></span>
                <InputText v-model="meta.cong_doan" />
              </label>
              <label class="stack-field">
                <span v-text="t('upload.site')"></span>
                <select v-if="sites.length" v-model="meta.site" class="native-select">
                  <option value="">Không chọn</option>
                  <option
                    v-for="site in sites"
                    :key="site.code"
                    :value="site.code"
                    v-text="site.name ? `${site.code} - ${site.name}` : site.code"
                  ></option>
                </select>
                <InputText v-else v-model="meta.site" />
              </label>
            </div>
          </section>

          <section class="upload-section">
            <details class="detail-block">
              <summary>Thông tin chi tiết dùng chung cho lô upload</summary>
              <div class="upload-grid detail-grid">
                <label class="stack-field">
                  <span>Tiêu đề</span>
                  <InputText v-model="detail.title" />
                </label>
                <label class="stack-field">
                  <span>Tags</span>
                  <InputText v-model="detail.tags" placeholder="Phân tách bởi dấu phẩy" />
                </label>
                <label class="stack-field">
                  <span>Số hiệu tài liệu</span>
                  <InputText v-model="detail.doc_number" />
                </label>
                <label class="stack-field">
                  <span>Người ký / chủ quản</span>
                  <InputText v-model="detail.owner_signer" />
                </label>
                <label class="stack-field">
                  <span>Ngày ban hành</span>
                  <input v-model="detail.issued_date" type="date" class="native-select" />
                </label>
                <label class="stack-field">
                  <span>Ngày hiệu lực</span>
                  <input v-model="detail.effective_date" type="date" class="native-select" />
                </label>
                <label class="stack-field">
                  <span>Ngày hết hạn</span>
                  <input v-model="detail.expiry_date" type="date" class="native-select" />
                </label>
                <label class="stack-field">
                  <span>Ngày rà soát</span>
                  <input v-model="detail.review_date" type="date" class="native-select" />
                </label>
                <label class="stack-field">
                  <span>Ngôn ngữ</span>
                  <input v-model="detail.language" list="upload-langs" class="native-select" />
                  <datalist id="upload-langs">
                    <option v-for="l in LANGUAGES" :key="l" :value="l"></option>
                  </datalist>
                </label>
                <label class="stack-field">
                  <span>Trạng thái hiệu lực</span>
                  <input v-model="detail.effective_status" list="upload-statuses" class="native-select" />
                  <datalist id="upload-statuses">
                    <option v-for="s in EFFECTIVE_STATUSES" :key="s" :value="s"></option>
                  </datalist>
                </label>
                <label class="stack-field full-span">
                  <span>Tóm tắt</span>
                  <textarea v-model="detail.summary" rows="3" class="native-select"></textarea>
                </label>
              </div>
            </details>
          </section>

          <div class="upload-submit">
            <div>
              <strong v-text="selectedFileSummary"></strong>
              <p class="muted-text">Sau khi tạo job, hệ thống sẽ chuyển sang trang tiến trình ingest.</p>
            </div>
            <Button type="submit" :label="t('upload.submit')" :loading="busy" />
          </div>
        </form>
      </template>
    </Card>
  </section>
</template>

<style scoped>
.mode-switch,
.check-row {
  display: flex;
  align-items: center;
  gap: 0.55rem;
}
.upload-layout {
  display: grid;
  grid-template-columns: minmax(320px, 0.9fr) minmax(520px, 1.4fr);
  gap: 1rem;
}
.upload-section {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
  min-width: 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(31, 42, 61, 0.45);
  padding: 1rem;
}
.upload-section h2 {
  margin: 0;
  font-size: 1rem;
}
.upload-section p {
  margin: 0.25rem 0 0;
}
.upload-drop {
  grid-row: span 2;
}
.section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 1rem;
}
.file-drop {
  display: grid;
  gap: 0.45rem;
  border: 1px dashed var(--border);
  border-radius: 8px;
  padding: 1rem;
  cursor: pointer;
}
.file-drop input {
  display: none;
}
.file-drop span {
  color: var(--accent);
  font-weight: 700;
}
.file-list {
  display: grid;
  gap: 0.45rem;
  max-height: 280px;
  overflow: auto;
}
.file-row {
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.55rem 0.65rem;
}
.file-row span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-row small {
  flex: 0 0 auto;
  color: var(--faint);
}
.upload-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
  gap: 0.85rem;
}
.checkbox-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 0.45rem 0.75rem;
  max-height: 260px;
  overflow: auto;
  padding-right: 0.25rem;
}
.mode-switch {
  flex-wrap: wrap;
  padding: 0.55rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
}
.per-file-list {
  display: grid;
  gap: 0.85rem;
  max-height: 420px;
  overflow: auto;
  padding-right: 0.25rem;
}
.per-file-row {
  display: grid;
  grid-template-columns: minmax(180px, 1fr) minmax(220px, 1fr);
  gap: 0.65rem;
  padding: 0.85rem;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.check-row {
  font-weight: 400;
}
.field-help {
  color: var(--faint);
  line-height: 1.45;
}
.detail-block summary {
  cursor: pointer;
  font-weight: 700;
}
.detail-grid {
  margin-top: 1rem;
}
.upload-submit {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: rgba(105, 214, 159, 0.08);
  padding: 1rem;
}
.upload-submit p {
  margin: 0.25rem 0 0;
}
@media (max-width: 1180px) {
  .upload-layout {
    grid-template-columns: 1fr;
  }
  .upload-drop {
    grid-row: auto;
  }
}
@media (max-width: 720px) {
  .section-heading,
  .upload-submit,
  .file-row {
    align-items: stretch;
    flex-direction: column;
  }
  .per-file-row {
    grid-template-columns: 1fr;
  }
}
</style>
