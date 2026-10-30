import { setActivePinia, createPinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore } from "@/stores/chat";

describe("chat memory", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("stores state memory per session", () => {
    const store = useChatStore();
    store.openSession("session-a");
    store.updateMemory("session-a", {
      currentPartIds: ["P-100"],
      conversationContext: { active: "doc" },
    });
    store.openSession("session-b");

    expect(store.currentMemory.currentPartIds).toEqual([]);

    store.openSession("session-a");
    expect(store.currentMemory.currentPartIds).toEqual(["P-100"]);
    expect(store.currentMemory.conversationContext).toEqual({ active: "doc" });
  });
});
