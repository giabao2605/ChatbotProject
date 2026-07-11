import { createRouter, createWebHistory, type RouterHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";
import { isRoleAllowed } from "@/authorization";
import ChatView from "@/views/ChatView.vue";
import DashboardView from "@/views/DashboardView.vue";
import LoginView from "@/views/LoginView.vue";
import DocumentsView from "@/views/DocumentsView.vue";
import UploadView from "@/views/UploadView.vue";
import QueueView from "@/views/QueueView.vue";
import ReviewView from "@/views/ReviewView.vue";
import AccessView from "@/views/AccessView.vue";
import UsersView from "@/views/UsersView.vue";
import OrgView from "@/views/OrgView.vue";
import GlossaryView from "@/views/GlossaryView.vue";
import MaterialsView from "@/views/MaterialsView.vue";
import LifecycleView from "@/views/LifecycleView.vue";
import SettingsView from "@/views/SettingsView.vue";
import FeedbackView from "@/views/FeedbackView.vue";
import RegressionView from "@/views/RegressionView.vue";
import QualityView from "@/views/QualityView.vue";
import AnalyticsView from "@/views/AnalyticsView.vue";
import ObservabilityView from "@/views/ObservabilityView.vue";
import AuditView from "@/views/AuditView.vue";
import HelpView from "@/views/HelpView.vue";

// meta.roles: capability duoc phep vao route. Rong = moi nguoi dung da dang
// nhap. Dong bo voi navItems trong App.vue de an menu va chan URL truc tiep.
export const routes = [
  { path: "/login", name: "login", component: LoginView, meta: { public: true } },
  { path: "/", redirect: "/chat" },
  { path: "/chat", name: "chat", component: ChatView },
  { path: "/dashboard", name: "dashboard", component: DashboardView, meta: { roles: ["platform_admin"] } },
  { path: "/documents", name: "documents", component: DocumentsView },
  { path: "/upload", name: "upload", component: UploadView, meta: { roles: ["uploader", "reviewer", "admin"] } },
  { path: "/queue", name: "queue", component: QueueView, meta: { roles: ["uploader", "reviewer", "admin"] } },
  { path: "/review", name: "review", component: ReviewView, meta: { roles: ["reviewer", "admin"] } },
  { path: "/access", name: "access", component: AccessView },
  { path: "/users", name: "users", component: UsersView, meta: { roles: ["security_admin"] } },
  { path: "/org", name: "org", component: OrgView, meta: { roles: ["platform_admin"] } },
  { path: "/materials", name: "materials", component: MaterialsView, meta: { roles: ["reviewer", "admin"] } },
  { path: "/glossary", name: "glossary", component: GlossaryView, meta: { roles: ["reviewer", "admin"] } },
  { path: "/lifecycle", name: "lifecycle", component: LifecycleView, meta: { roles: ["reviewer", "admin"] } },
  { path: "/feedback", name: "feedback", component: FeedbackView, meta: { roles: ["reviewer", "admin"] } },
  { path: "/regression", name: "regression", component: RegressionView, meta: { roles: ["reviewer", "admin"] } },
  { path: "/quality", name: "quality", component: QualityView, meta: { roles: ["reviewer", "admin"] } },
  { path: "/analytics", name: "analytics", component: AnalyticsView, meta: { roles: ["reviewer", "admin"] } },
  { path: "/observability", name: "observability", component: ObservabilityView, meta: { roles: ["platform_admin"] } },
  { path: "/audit", name: "audit", component: AuditView, meta: { roles: ["platform_admin"] } },
  { path: "/settings", name: "settings", component: SettingsView, meta: { roles: ["platform_admin"] } },
  { path: "/help", name: "help", component: HelpView },
];

export function createAppRouter(history: RouterHistory = createWebHistory()) {
  const router = createRouter({ history, routes });

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
    // Chan truy cap route theo vai tro (kem an menu o App.vue).
    const roles = (to.meta.roles as string[] | undefined) ?? [];
    if (roles.length && auth.user) {
      if (!isRoleAllowed(auth.user.roles, roles)) return { name: "chat" };
    }
    return true;
  });

  return router;
}

export const router = createAppRouter();
