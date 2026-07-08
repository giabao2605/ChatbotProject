<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, RouterView, useRouter } from "vue-router";
import { useAuthStore } from "@/stores/auth";

const auth = useAuthStore();
const router = useRouter();

const navItems = computed(() => [
  { to: "/chat", label: "Chatbot hỏi đáp", roles: [] },
  { to: "/dashboard", label: "Tổng quan", roles: [] },
  { to: "/documents", label: "Kho tài liệu", roles: ["reviewer", "admin"] },
  { to: "/upload", label: "Tải tài liệu", roles: ["uploader", "admin"] },
  { to: "/queue", label: "Tiến trình ingest", roles: ["uploader", "admin"] },
  { to: "/review", label: "Duyệt tài liệu", roles: ["reviewer", "admin"] },
  { to: "/access", label: "Yêu cầu quyền", roles: [] },
  { to: "/users", label: "Người dùng", roles: ["admin"] },
  { to: "/analytics", label: "Báo cáo sử dụng", roles: ["reviewer", "admin"] },
  { to: "/observability", label: "Observability", roles: ["admin"] },
  { to: "/audit", label: "Audit Log", roles: ["admin"] },
  { to: "/settings", label: "Cấu hình", roles: ["admin"] },
  { to: "/help", label: "Hướng dẫn", roles: [] },
]);

function canShow(roles: string[]) {
  if (!auth.user || roles.length === 0) return true;
  if (auth.user.roles.includes("admin")) return true;
  return roles.some((role) => auth.user?.roles.includes(role));
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
          <div class="brand-title">Trợ lý tài liệu nội bộ</div>
          <div class="brand-subtitle">RAG & quản trị dữ liệu</div>
        </div>
      </div>

      <nav class="nav">
        <RouterLink
          v-for="item in navItems.filter((entry) => canShow(entry.roles))"
          :key="item.to"
          :to="item.to"
          class="nav-link"
        >
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="account-panel">
        <div class="account-name">{{ auth.user.display_name || auth.user.username }}</div>
        <div class="account-meta">{{ auth.user.department || "Chưa gán phòng ban" }}</div>
        <div class="account-meta">{{ auth.user.roles.join(", ") || "Chưa gán role" }}</div>
        <Button label="Đăng xuất" severity="secondary" outlined class="logout-button" @click="doLogout" />
      </div>
    </aside>

    <main class="main-panel">
      <RouterView />
    </main>
  </div>
</template>
