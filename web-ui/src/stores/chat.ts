import { defineStore } from "pinia";
import type { SessionMemory } from "@/types";

function createSessionId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export const useChatStore = defineStore("chat", {
  state: () => ({
    sessionId: createSessionId(),
    memoryBySession: {} as Record<string, SessionMemory>,
  }),
  getters: {
    currentMemory(state): SessionMemory {
      return state.memoryBySession[state.sessionId] ?? {
        currentPartIds: [],
        conversationContext: null,
      };
    },
  },
  actions: {
    newSession() {
      this.sessionId = createSessionId();
    },
    openSession(sessionId: string) {
      this.sessionId = sessionId;
      if (!this.memoryBySession[sessionId]) {
        this.memoryBySession[sessionId] = {
          currentPartIds: [],
          conversationContext: null,
        };
      }
    },
    updateMemory(sessionId: string, memory: SessionMemory) {
      this.memoryBySession[sessionId] = memory;
    },
    clearMemory(sessionId?: string) {
      const targetSessionId = sessionId ?? this.sessionId;
      this.memoryBySession[targetSessionId] = {
        currentPartIds: [],
        conversationContext: null,
      };
    },
  },
});
