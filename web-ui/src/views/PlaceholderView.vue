<script setup lang="ts">
import { computed } from "vue";
import { useRoute } from "vue-router";

const route = useRoute();
const title = computed(() => String(route.meta.title || "Chức năng"));
</script>

<template>
  <section class="content-page">
    <header class="page-header">
      <div>
        <div class="eyebrow">Migration slice</div>
        <h1>{{ title }}</h1>
      </div>
    </header>
    <Card>
      <template #content>
        <p>
          API nền cho nhóm chức năng này đã được đặt trong app-api. Màn hình Vue chi tiết sẽ được port theo
          thứ tự rủi ro sau khi chat và file-serving đạt parity.
        </p>
      </template>
    </Card>
  </section>
</template>
