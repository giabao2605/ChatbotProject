import { describe, expect, it } from "vitest";
import { parseSseBuffer } from "@/api/sse";

describe("parseSseBuffer", () => {
  it("parses complete events and preserves partial tail", () => {
    const parsed = parseSseBuffer(
      'event: delta\ndata: {"text":"hello"}\n\nevent: done\ndata: {"ok":true}',
    );

    expect(parsed.events).toEqual([{ event: "delta", data: { text: "hello" } }]);
    expect(parsed.rest).toBe('event: done\ndata: {"ok":true}');
  });
});
