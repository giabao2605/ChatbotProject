<script setup lang="ts">
import { RouterLink } from "vue-router";
import { t } from "@/i18n";
</script>

<template>
  <section class="content-page">
    <header class="page-header">
      <div>
        <h1 v-text="t('notFound.title')"></h1>
        <p class="page-subtitle" v-text="t('notFound.description')"></p>
      </div>
    </header>
    <RouterLink to="/dashboard" v-text="t('notFound.back')"></RouterLink>
  </section>
</template>
