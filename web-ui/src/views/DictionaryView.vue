<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import MaterialsView from "@/views/MaterialsView.vue";
import GlossaryView from "@/views/GlossaryView.vue";

type DictionaryTab = "materials" | "glossary";
const route = useRoute();
const router = useRouter();
const activeTab = computed<DictionaryTab>(() => route.query.tab === "glossary" ? "glossary" : "materials");

function selectTab(tab: DictionaryTab) {
  void router.replace({ path: "/dictionary", query: { tab } });
}
</script>

<template>
  <section class="content-page dictionary-shell">
    <header class="page-header">
      <div>
        <div class="eyebrow">Dictionary</div>
        <h1>Từ điển</h1>
        <p class="page-subtitle">Quản lý mã vật tư và thuật ngữ dùng trong tìm kiếm.</p>
      </div>
    </header>
    <nav class="view-tabs" aria-label="Loại từ điển">
      <Button label="Từ điển vật tư" :outlined="activeTab !== 'materials'" @click="selectTab('materials')" />
      <Button label="Thuật ngữ và đồng nghĩa" :outlined="activeTab !== 'glossary'" @click="selectTab('glossary')" />
    </nav>
  </section>
  <MaterialsView v-if="activeTab === 'materials'" />
  <GlossaryView v-else />
</template>

<style scoped>
.dictionary-shell { padding-bottom: 0; }
.view-tabs { display: flex; flex-wrap: wrap; gap: .65rem; }
</style>
