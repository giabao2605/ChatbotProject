import { createRouter, createWebHistory } from "vue-router";
import { useAuthStore } from "@/stores/auth";
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

const routes = [
  { path: "/login", name: "login", component: LoginView, meta: { public: true } },
  { path: "/", redirect: "/chat" },
  { path: "/chat", name: "chat", component: ChatView },
  { path: "/dashboard", name: "dashboard", component: DashboardView },
  { path: "/documents", name: "documents", component: DocumentsView },
  { path: "/upload", name: "upload", component: UploadView },
  { path: "/queue", name: "queue", component: QueueView },
  { path: "/review", name: "review", component: ReviewView },
  { path: "/access", name: "access", component: AccessView },
  { path: "/users", name: "users", component: UsersView },
  { path: "/org", name: "org", component: OrgView },
  { path: "/materials", name: "materials", component: MaterialsView },
  { path: "/glossary", name: "glossary", component: GlossaryView },
  { path: "/lifecycle", name: "lifecycle", component: LifecycleView },
  { path: "/feedback", name: "feedback", component: FeedbackView },
  { path: "/regression", name: "regression", component: RegressionView },
  { path: "/quality", name: "quality", component: QualityView },
  { path: "/analytics", name: "analytics", component: AnalyticsView },
  { path: "/observability", name: "observability", component: ObservabilityView },
  { path: "/audit", name: "audit", component: AuditView },
  { path: "/settings", name: "settings", component: SettingsView },
  { path: "/help", name: "help", component: HelpView },
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
