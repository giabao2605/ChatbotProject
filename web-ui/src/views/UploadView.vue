<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { apiGet, apiUpload } from "@/api/client";
import { t } from "@/i18n";
import { SECURITY_LEVELS } from "@/utils/rows";
import type { ApiRow } from "@/types";

const router = useRouter();
const file = ref<File | null>(null);
const busy = ref(false);
const error = ref("");
const notice = ref("");

// Danh muc phong ban / site dang active (goi y cho nguoi dung, giong ban Streamlit cu).
const departments = ref<Array<{ code: string; name: string }>>([]);
const sites = ref<Array<{ code: string; name: string }>>([]);
const catalogError = ref("");

// Goi y gia tri cho vai truong metadata.
const LANGUAGES = ["vi", "en", "ja", "zh", "ko"];
const EFFECTIVE_STATUSES = ["draft", "effective", "superseded", "expired", "withdrawn"];

const meta = reactive({
  thu_muc: "",
  domain: "",
  security_level: "internal",
  cong_doan: "",
  site: "",
});

// GD2: cac truong metadata chi tiet (tuy chon) -> gom vao meta_json gui kem upload.
// Key trung voi _COMMON_META_COLS o backend (title/summary/tags/doc_number/cac moc ngay/...).
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

function pick(row: ApiRow, keys: string[]): string {
  for (const k of keys) {
    const found = Object.keys(row).find((rk) => rk.toLowerCase() === k.toLowerCase());
    if (found && row[found] != null && String(row[found]).trim()) return String(row[found]);
  }
  return "";
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
    // Khong chan viec upload neu catalog loi; van cho nhap tay ben duoi.
    catalogError.value = err instanceof Error ? err.message : t("common.error");
  }
}
onMounted(loadCatalog);

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  file.value = input.files && input.files.length ? input.files[0] : null;
}

async function submit() {
  error.value = "";
  notice.value = "";
  if (!file.value) {
    error.value = t("upload.noFile");
    return;
  }
  if (!meta.thu_muc.trim()) {
    error.value = t("upload.noDept");
    return;
  }
  busy.value = true;
  try {
    const form = new FormData();
    form.append("file", file.value);
    form.append("thu_muc", meta.thu_muc.trim());
    if (meta.domain) form.append("domain", meta.domain);
    if (meta.security_level) form.append("security_level", meta.security_level);
    if (meta.cong_doan) form.append("cong_doan", meta.cong_doan);
    if (meta.site) form.append("site", meta.site);

    // Gom cac truong chi tiet khong rong vao meta_json.
    const metaJson: Record<string, string> = {};
    for (const [key, value] of Object.entries(detail)) {
      const trimmed = String(value ?? "").trim();
      if (trimmed) metaJson[key] = trimmed;
    }
    if (Object.keys(metaJson).length) form.append("meta_json", JSON.stringify(metaJson));

    const result = await apiUpload<{ job_id: number }>("/api/documents/upload", form);
    notice.value = t("upload.success", { id: String(result.job_id) });
    setTimeout(() => router.push("/queue"), 800);
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
    </header>

    <Message v-if="error" severity="error" v-text="error"></Message>
    <Message v-if="notice && !error" severity="success" v-text="notice"></Message>
    <Message v-if="catalogError" severity="warn" v-text="catalogError"></Message>

    <Card>
      <template #content>
        <form class="stack-form" @submit.prevent="submit">
          <label class="stack-field">
            <span v-text="t('upload.file')"></span>
            <input type="file" accept=".pdf,.docx,.doc,.xlsx,.xls,.txt,.md,.csv,.pptx" @change="onFileChange" />
          </label>
          <label class="stack-field">
            <span v-text="t('upload.dept')"></span>
            <select v-if="departments.length" v-model="meta.thu_muc" class="native-select">
              <option value="" disabled>— Chọn phòng ban —</option>
              <option
                v-for="dept in departments"
                :key="dept.code"
                :value="dept.code"
                v-text="dept.name ? `${dept.code} — ${dept.name}` : dept.code"
              ></option>
            </select>
            <InputText v-else v-model="meta.thu_muc" placeholder="VD: CoKhi" />
          </label>
          <label class="stack-field">
            <span v-text="t('upload.domain')"></span>
            <InputText v-model="meta.domain" />
          </label>
          <label class="stack-field">
            <span v-text="t('upload.security')"></span>
            <select v-model="meta.security_level" class="native-select">
              <option v-for="opt in SECURITY_LEVELS" :key="opt.value" :value="opt.value" v-text="opt.label"></option>
            </select>
          </label>
          <label class="stack-field">
            <span v-text="t('upload.stage')"></span>
            <InputText v-model="meta.cong_doan" />
          </label>
          <label class="stack-field">
            <span v-text="t('upload.site')"></span>
            <select v-if="sites.length" v-model="meta.site" class="native-select">
              <option value="">— Không chọn —</option>
              <option
                v-for="site in sites"
                :key="site.code"
                :value="site.code"
                v-text="site.name ? `${site.code} — ${site.name}` : site.code"
              ></option>
            </select>
            <InputText v-else v-model="meta.site" />
          </label>

          <details class="detail-block">
            <summary>Thông tin chi tiết (tuỳ chọn)</summary>
            <label class="stack-field">
              <span>Tiêu đề</span>
              <InputText v-model="detail.title" />
            </label>
            <label class="stack-field">
              <span>Tóm tắt</span>
              <textarea v-model="detail.summary" rows="3" class="native-select"></textarea>
            </label>
            <label class="stack-field">
              <span>Tags (phân tách bởi dấu phẩy)</span>
              <InputText v-model="detail.tags" />
            </label>
            <label class="stack-field">
              <span>Số hiệu tài liệu</span>
              <InputText v-model="detail.doc_number" />
            </label>
            <label class="stack-field">
              <span>Ngày ban hành</span>
              <input type="date" v-model="detail.issued_date" class="native-select" />
            </label>
            <label class="stack-field">
              <span>Ngày hiệu lực</span>
              <input type="date" v-model="detail.effective_date" class="native-select" />
            </label>
            <label class="stack-field">
              <span>Ngày hết hạn</span>
              <input type="date" v-model="detail.expiry_date" class="native-select" />
            </label>
            <label class="stack-field">
              <span>Ngày rà soát</span>
              <input type="date" v-model="detail.review_date" class="native-select" />
            </label>
            <label class="stack-field">
              <span>Người ký / chủ quản</span>
              <InputText v-model="detail.owner_signer" />
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
          </details>

          <div class="form-actions">
            <Button type="submit" :label="t('upload.submit')" :loading="busy" />
          </div>
        </form>
      </template>
    </Card>
  </section>
</template>
