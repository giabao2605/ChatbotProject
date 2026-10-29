<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { t } from "@/i18n";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const username = ref("");
const password = ref("");
const validationError = ref("");

async function submit() {
  validationError.value = "";
  if (!username.value.trim() || !password.value) {
    validationError.value = t("login.required");
    return;
  }
  try {
    await auth.login(username.value, password.value);
  } catch {
    // The auth store provides the user-facing error.
    return;
  }
  await router.push(String(route.query.next || "/dashboard"));
}
</script>

<template>
  <section class="login-page">
    <Card class="login-card">
      <template #title>{{ t('login.title') }}</template>
      <template #subtitle>{{ t('login.subtitle') }}</template>
      <template #content>
        <form class="login-form" @submit.prevent="submit">
          <label>
            <span>{{ t('login.username') }}</span>
            <InputText v-model="username" autocomplete="username" autofocus />
          </label>
          <label>
            <span>{{ t('login.password') }}</span>
            <Password v-model="password" :feedback="false" :prompt-label="t('login.passwordPrompt')" toggle-mask autocomplete="current-password" />
          </label>
          <Message v-if="validationError" severity="error">{{ validationError }}</Message>
          <Message v-if="auth.error" severity="error">{{ auth.error }}</Message>
          <Button type="submit" :label="t('login.submit')" :loading="auth.loading" />
        </form>
      </template>
    </Card>
  </section>
</template>
