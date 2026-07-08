<script setup lang="ts">
import { reactive, ref } from "vue";
import { useRouter } from "vue-router";
import { apiUpload } from "@/api/client";
import { t } from "@/i18n";
import { SECURITY_LEVELS } from "@/utils/rows";

const router = useRouter();
const file = ref<File | null>(null);
const busy = ref(false);
const error = ref("");
const notice = ref("");

const meta = reactive({
  thu_muc: "",
  domain: "",
  security_level: "internal",
  cong_doan: "",
  site: "",
});

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

    <Card>
      <template #content>
        <form class="stack-form" @submit.prevent="submit">
          <label class="stack-field">
            <span v-text="t('upload.file')"></span>
            <input type="file" accept=".pdf,.docx,.doc,.xlsx,.xls,.txt,.md,.csv,.pptx" @change="onFileChange" />
          </label>
          <label class="stack-field">
            <span v-text="t('upload.dept')"></span>
            <InputText v-model="meta.thu_muc" placeholder="VD: CoKhi" />
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
            <InputText v-model="meta.site" />
          </label>
          <div class="form-actions">
            <Button type="submit" :label="t('upload.submit')" :loading="busy" />
          </div>
        </form>
      </template>
    </Card>
  </section>
</template>
