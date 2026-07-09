import { fileURLToPath, URL } from "node:url";
import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [vue()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/@primevue/themes")) {
            return "primevue-theme";
          }
          if (id.includes("node_modules/primevue/datatable") || id.includes("node_modules/primevue/column")) {
            return "primevue-table";
          }
          if (id.includes("node_modules/primevue/dialog")) {
            return "primevue-dialog";
          }
          if (id.includes("node_modules/primevue")) {
            return "primevue-core";
          }
          if (
            id.includes("node_modules/vue") ||
            id.includes("node_modules/vue-router") ||
            id.includes("node_modules/pinia") ||
            id.includes("node_modules/@tanstack")
          ) {
            return "vue";
          }
          return undefined;
        },
      },
    },
  },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8080",
    },
  },
});
