import { defineStore } from "pinia";
import * as api from "@/api/client";
import { setLocale } from "@/i18n";
import type { Locale } from "@/i18n";
import type { UserProfile } from "@/types";

function syncLocale(user: UserProfile | null) {
  const pref = user?.preferred_language;
  if (pref === "vi" || pref === "en") setLocale(pref);
}

export const useAuthStore = defineStore("auth", {
  state: () => ({
    user: null as UserProfile | null,
    ready: false,
    loading: false,
    error: "",
  }),
  getters: {
    isAdmin: (state) => state.user?.roles.includes("admin") ?? false,
  },
  actions: {
    async loadMe() {
      try {
        this.user = await api.loadMe();
        syncLocale(this.user);
      } catch {
        this.user = null;
      } finally {
        this.ready = true;
      }
    },
    async login(username: string, password: string) {
      this.loading = true;
      this.error = "";
      try {
        this.user = await api.login(username, password);
        syncLocale(this.user);
        this.ready = true;
      } catch (error) {
        this.error = error instanceof Error ? error.message : "Đăng nhập thất bại";
        throw error;
      } finally {
        this.loading = false;
      }
    },
    async refresh() {
      try {
        this.user = await api.refreshSession();
        syncLocale(this.user);
        this.ready = true;
        return true;
      } catch {
        return false;
      }
    },
    async setLanguage(locale: Locale) {
      setLocale(locale);
      if (this.user) this.user.preferred_language = locale;
      try {
        await api.updatePreferredLanguage(locale);
      } catch {
        /* preference persists locally even if the server call fails */
      }
    },
    async logout() {
      await api.logout();
      this.user = null;
    },
  },
});
