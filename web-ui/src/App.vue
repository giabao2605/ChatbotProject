<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, RouterView, useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { t, currentLocale } from "@/i18n";
import type { Locale } from "@/i18n";

const auth = useAuthStore();
const router = useRouter();

const navItems = computed(() => [
  { to: "/chat", key: "nav.chat", roles: [] as string[] },
  { to: "/dashboard", key: "nav.dashboard", roles: [] as string[] },
  { to: "/documents", key: "nav.documents", roles: [] as string[] },
  { to: "/upload", key: "nav.upload", roles: ["uploader", "admin"] },
  { to: "/queue", key: "nav.queue", roles: ["uploader", "reviewer", "admin"] },
  { to: "/review", key: "nav.review", roles: ["reviewer", "admin"] },
  { to: "/access", key: "nav.access", roles: [] as string[] },
  { to: "/users", key: "nav.users", roles: ["admin"] },
  { to: "/org", key: "nav.org", roles: ["admin"] },
  { to: "/glossary", key: "nav.glossary", roles: ["reviewer", "admin"] },
  { to: "/materials", key: "nav.materials", roles: ["reviewer", "admin"] },
  { to: "/lifecycle", key: "nav.lifecycle", roles: ["reviewer", "admin"] },
  { to: "/feedback", key: "nav.feedback", roles: ["reviewer", "admin"] },
  { to: "/regression", key: "nav.regression", roles: ["reviewer", "admin"] },
  { to: "/quality", key: "nav.quality", roles: ["reviewer", "admin"] },
  { to: "/analytics", key: "nav.analytics", roles: ["reviewer", "admin"] },
  { to: "/observability", key: "nav.observability", roles: ["admin"] },
  { to: "/audit", key: "nav.audit", roles: ["admin"] },
  { to: "/settings", key: "nav.settings", roles: ["admin"] },
  { to: "/help", key: "nav.help", roles: [] as string[] },
]);

function canShow(roles: string[]) {
  if (!auth.user || roles.length === 0) return true;
  if (auth.user.roles.includes("admin")) return true;
  return roles.some((role) => auth.user?.roles.includes(role));
}

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
  <div class="app-dark app-shell">
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
          v-for="item in navItems.filter((entry) => canShow(entry.roles))"
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
