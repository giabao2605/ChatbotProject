<script setup lang="ts">
import { ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const route = useRoute();
const router = useRouter();
const username = ref("");
const password = ref("");

async function submit() {
  await auth.login(username.value, password.value);
  await router.push(String(route.query.next || "/dashboard"));
}
</script>

<template>
  <section class="login-page">
    <Card class="login-card">
      <template #title>Đăng nhập hệ thống</template>
      <template #subtitle>Truy cập trợ lý tài liệu nội bộ</template>
      <template #content>
        <form class="login-form" @submit.prevent="submit">
          <label>
            <span>Tên đăng nhập</span>
            <InputText v-model="username" autocomplete="username" autofocus />
          </label>
          <label>
            <span>Mật khẩu</span>
            <Password v-model="password" :feedback="false" toggle-mask autocomplete="current-password" />
          </label>
          <Message v-if="auth.error" severity="error">{{ auth.error }}</Message>
          <Button type="submit" label="Đăng nhập" :loading="auth.loading" />
        </form>
      </template>
    </Card>
  </section>
</template>
