import { describe, expect, it } from "vitest";
import { renderMarkdown } from "@/utils/markdown";

describe("renderMarkdown", () => {
  it("renders markdown tables for assistant answers", () => {
    const html = renderMarkdown(
      [
        "## Bảng doanh thu",
        "",
        "| Tháng | Sản phẩm | Doanh thu |",
        "|---|---|---|",
        "| 04/2026 | **SP-A02** | `90000000` |",
      ].join("\n"),
    );

    expect(html).toContain("<h4>Bảng doanh thu</h4>");
    expect(html).toContain('<div class="md-table-wrap">');
    expect(html).toContain("<th>Tháng</th>");
    expect(html).toContain("<td><strong>SP-A02</strong></td>");
    expect(html).toContain("<td><code>90000000</code></td>");
  });

  it("escapes raw html before applying lightweight markdown", () => {
    const html = renderMarkdown("<script>alert(1)</script> **ok**");

    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain("<strong>ok</strong>");
    expect(html).not.toContain("<script>");
  });
});
