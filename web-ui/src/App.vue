<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, RouterView, useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { t, currentLocale } from "@/i18n";
import type { Locale } from "@/i18n";
import { visibleNavigationItems } from "@/authorization";

const auth = useAuthStore();
const router = useRouter();

const navItems = computed(() => visibleNavigationItems(auth.user?.roles));

const accountName = computed(() => auth.user?.display_name || auth.user?.username || "");
const accountRoles = computed(() => (auth.user?.roles || []).join(", "));
const accountMeta = computed(() => {
  const parts = [auth.user?.department, auth.user?.max_security_level].filter(Boolean);
  return parts.join(" · ");
});

function switchLocale(locale: Locale) {
  auth.setLanguage(locale);
}
function isLocale(locale: Locale) {
  return currentLocale() === locale;
}

async function doLogout() {
  await auth.logout();
  await router.push("/login");
}
</script>

<template>
  <div class="app-dark app-shell" :class="{ 'has-sidebar': auth.user }">
    <aside v-if="auth.user" class="sidebar">
      <div class="brand">
        <div class="brand-mark">ID</div>
        <div>
          <div class="brand-title" v-text="t('brand.title')"></div>
          <div class="brand-subtitle" v-text="t('brand.subtitle')"></div>
        </div>
      </div>

      <nav class="nav">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="nav-link"
          v-text="t(item.key)"
        ></RouterLink>
      </nav>

      <div class="account-panel">
        <div class="account-name" v-text="accountName"></div>
        <div class="account-meta" v-text="accountRoles"></div>
        <div v-if="accountMeta" class="account-meta" v-text="accountMeta"></div>
        <div class="lang-switch">
          <button
            type="button"
            class="lang-button"
            :class="{ active: isLocale('vi') }"
            @click="switchLocale('vi')"
          >VI</button>
          <button
            type="button"
            class="lang-button"
            :class="{ active: isLocale('en') }"
            @click="switchLocale('en')"
          >EN</button>
        </div>
        <Button
          :label="t('common.logout')"
          severity="secondary"
          outlined
          class="logout-button"
          @click="doLogout"
        />
      </div>
    </aside>

    <main class="main-panel">
      <RouterView />
    </main>
  </div>
</template>
