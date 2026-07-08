import { createApp } from "vue";
import { createPinia } from "pinia";
import { VueQueryPlugin } from "@tanstack/vue-query";
import PrimeVue from "primevue/config";
import Aura from "@primevue/themes/aura";
import Button from "primevue/button";
import Card from "primevue/card";
import Column from "primevue/column";
import DataTable from "primevue/datatable";
import InputText from "primevue/inputtext";
import Message from "primevue/message";
import Password from "primevue/password";
import ProgressSpinner from "primevue/progressspinner";
import Tag from "primevue/tag";
import Textarea from "primevue/textarea";
import App from "./App.vue";
import { router } from "./router";
import "./styles.css";

const app = createApp(App);

app.use(createPinia());
app.use(VueQueryPlugin);
app.use(router);
app.use(PrimeVue, {
  theme: {
    preset: Aura,
    options: {
      darkModeSelector: ".app-dark",
    },
  },
});

app.component("Button", Button);
app.component("Card", Card);
app.component("Column", Column);
app.component("DataTable", DataTable);
app.component("InputText", InputText);
app.component("Message", Message);
app.component("Password", Password);
app.component("ProgressSpinner", ProgressSpinner);
app.component("Tag", Tag);
app.component("Textarea", Textarea);

app.mount("#app");
