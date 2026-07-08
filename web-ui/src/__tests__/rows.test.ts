import { describe, expect, it } from "vitest";
import { num, str, bool, pickField, pickStr, pickNum } from "@/utils/rows";

describe("row helpers", () => {
  it("reads named + positional fields", () => {
    const dictRow = { DocID: 7, Title: "A", IsCurrent: true };
    expect(num(dictRow, "DocID")).toBe(7);
    expect(str(dictRow, "Title")).toBe("A");
    expect(bool(dictRow, "IsCurrent")).toBe(true);

    const arrayRow = { c0: 12, c1: "file.pdf" };
    expect(num(arrayRow, "c0")).toBe(12);
    expect(str(arrayRow, "c1")).toBe("file.pdf");
  });

  it("str returns empty string for null/undefined", () => {
    expect(str({ x: null }, "x")).toBe("");
    expect(str({}, "missing")).toBe("");
  });

  it("pickField resolves by exact then fuzzy candidate", () => {
    expect(pickField({ GlossaryID: 3 }, ["glossaryid"])).toBe(3); // case-insensitive exact
    expect(pickStr({ RequestID: 9 }, ["request"])).toBe("9"); // fuzzy contains
    expect(pickNum({ UserID: 5 }, ["userid"])).toBe(5);
    expect(pickField({ a: 1 }, ["zzz"])).toBeUndefined();
  });
});
