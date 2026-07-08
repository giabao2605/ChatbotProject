import { describe, expect, it } from "vitest";
import { setLocale, currentLocale, t } from "@/i18n";

describe("i18n", () => {
  it("switches locale and returns localized strings", () => {
    setLocale("vi");
    expect(currentLocale()).toBe("vi");
    expect(t("nav.documents")).toBe("Kho tài liệu");
    setLocale("en");
    expect(currentLocale()).toBe("en");
    expect(t("nav.documents")).toBe("Document library");
  });

  it("falls back to the key when a translation is missing", () => {
    setLocale("en");
    expect(t("does.not.exist")).toBe("does.not.exist");
  });

  it("substitutes named params", () => {
    setLocale("vi");
    expect(t("upload.success", { id: "42" })).toBe("Đã tạo job ingest #42");
  });
});
