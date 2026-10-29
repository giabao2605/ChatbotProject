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
    sessionRevision: 0,
    restoring: null as { controller: AbortController; promise: Promise<void> } | null,
  }),
  getters: {
    isAdmin: (state) => state.user?.roles.includes("admin") ?? false,
  },
  actions: {
    async loadMe() {
      if (this.restoring) return this.restoring.promise;
      const revision = this.sessionRevision;
      const controller = new AbortController();
      const promise = (async () => {
        try {
          const user = await api.loadMe(controller.signal);
          if (revision === this.sessionRevision) {
            this.user = user;
            syncLocale(user);
          }
        } catch {
          if (revision === this.sessionRevision) this.user = null;
        } finally {
          if (revision === this.sessionRevision) this.ready = true;
          if (this.restoring?.controller === controller) this.restoring = null;
        }
      })();
      this.restoring = { controller, promise };
      return promise;
    },
    async login(username: string, password: string) {
      this.sessionRevision += 1;
      this.restoring?.controller.abort();
      this.restoring = null;
      this.loading = true;
      this.error = "";
      try {
        this.user = await api.login(username, password);
        syncLocale(this.user);
        this.ready = true;
      } catch (error) {
        this.error = api.loginErrorMessage(error);
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
      this.sessionRevision += 1;
      this.restoring?.controller.abort();
      this.restoring = null;
      this.user = null;
      this.ready = true;
      await api.logout();
      this.user = null;
      this.ready = true;
    },
  },
});
