import { defineComponent } from "vue";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import QualityView from "@/views/QualityView.vue";

describe("quality score label", () => {
  it("identifies feedback as the score source", () => {
    const ResourcePage = defineComponent({
      props: { description: String },
      template: "<p>{{ description }}</p>",
    });
    const wrapper = mount(QualityView, { global: { stubs: { ResourcePage } } });

    expect(wrapper.text()).toContain("phản hồi người dùng");
    expect(wrapper.text()).not.toContain("trích xuất");
  });
});
