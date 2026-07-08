import { defineStore } from "pinia";
import * as api from "@/api/client";
import type { UserProfile } from "@/types";

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
        this.ready = true;
      } catch (error) {
        this.error = error instanceof Error ? error.message : "Đăng nhập thất bại";
        throw error;
      } finally {
        this.loading = false;
      }
    },
    async logout() {
      await api.logout();
      this.user = null;
    },
  },
});
