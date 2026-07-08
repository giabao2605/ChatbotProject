import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import ChatView from "@/views/ChatView.vue";
import DashboardView from "@/views/DashboardView.vue";
import LoginView from "@/views/LoginView.vue";
import PlaceholderView from "@/views/PlaceholderView.vue";

const routes = [
  { path: "/login", name: "login", component: LoginView, meta: { public: true } },
  { path: "/", redirect: "/chat" },
  { path: "/chat", name: "chat", component: ChatView },
  { path: "/dashboard", name: "dashboard", component: DashboardView },
  { path: "/documents", name: "documents", component: PlaceholderView, meta: { title: "Kho tài liệu" } },
  { path: "/upload", name: "upload", component: PlaceholderView, meta: { title: "Tải tài liệu" } },
  { path: "/queue", name: "queue", component: PlaceholderView, meta: { title: "Tiến trình ingest" } },
  { path: "/review", name: "review", component: PlaceholderView, meta: { title: "Duyệt tài liệu" } },
  { path: "/access", name: "access", component: PlaceholderView, meta: { title: "Yêu cầu quyền" } },
  { path: "/users", name: "users", component: PlaceholderView, meta: { title: "Người dùng" } },
  { path: "/materials", name: "materials", component: PlaceholderView, meta: { title: "Từ điển vật tư" } },
  { path: "/glossary", name: "glossary", component: PlaceholderView, meta: { title: "Từ điển đồng nghĩa" } },
  { path: "/lifecycle", name: "lifecycle", component: PlaceholderView, meta: { title: "Vòng đời tài liệu" } },
  { path: "/feedback", name: "feedback", component: PlaceholderView, meta: { title: "Feedback Loop" } },
  { path: "/analytics", name: "analytics", component: PlaceholderView, meta: { title: "Báo cáo sử dụng" } },
  { path: "/observability", name: "observability", component: PlaceholderView, meta: { title: "Observability" } },
  { path: "/audit", name: "audit", component: PlaceholderView, meta: { title: "Audit Log" } },
  { path: "/settings", name: "settings", component: PlaceholderView, meta: { title: "Cấu hình" } },
  { path: "/help", name: "help", component: PlaceholderView, meta: { title: "Hướng dẫn" } },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach(async (to) => {
  const auth = useAuthStore();
  if (!auth.ready) {
    await auth.loadMe();
  }
  if (!to.meta.public && !auth.user) {
    return { name: "login", query: { next: to.fullPath } };
  }
  if (to.meta.public && auth.user) {
    return { name: "chat" };
  }
  return true;
});
