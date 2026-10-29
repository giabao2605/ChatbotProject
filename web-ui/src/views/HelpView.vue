<script setup lang="ts">
import { t } from "@/i18n";
</script>

<template>
  <section class="content-page">
    <header class="page-header">
      <div>
        <div class="eyebrow">Help</div>
        <h1 v-text="t('help.title')"></h1>
        <p class="page-subtitle" v-text="t('help.subtitle')"></p>
      </div>
    </header>

    <Card>
      <template #content>
        <div class="help-body">
          <template v-for="step in 6" :key="step">
            <h3 v-text="t(`help.step${step}.title`)"></h3>
            <p v-text="t(`help.step${step}.body`)"></p>
          </template>
        </div>
      </template>
    </Card>
  </section>
</template>
