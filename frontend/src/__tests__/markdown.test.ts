import { describe, expect, it } from "vitest";
import { formatSourceText, renderMarkdown } from "@/utils/markdown";

describe("renderMarkdown", () => {
  it('preserves numbered steps separated by citations and resolves locator references safely', () => {
    const html = renderMarkdown('1. Nhận\n\n[SRC:D17L1234ABCD]\n\n2. Duyệt', [{
      doc_id:17,page_no:null,source_id:'D17L1234ABCD',file_name:'<ledger>.xlsx',has_vision:false,
      original_url:'/original',locator:{doc_id:'17',version:'1',sheet:'Revenue',cell:'B2'},
    }]);
    expect(html).toContain('<ol start="2">');
    expect(html).toContain('&lt;ledger&gt;.xlsx · Sheet Revenue · Ô B2');
    expect(html).not.toContain('[SRC:');
    expect(html).not.toContain('<ledger>');
    expect(renderMarkdown('[SRC:D17L1234ABCD]')).toContain('Nguồn tài liệu D17');
  });
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

  it("turns legacy source markdown and citation markers into readable source text", () => {
    const text = formatSourceText(
      ["---", "**Nguồn tham chiếu:**", "- **manual.pdf** (Trang 3)", "[SRC:D134P1]"].join("\n"),
    );

    expect(text).toContain("Nguồn tham chiếu:");
    expect(text).toContain("manual.pdf (Trang 3)");
    expect(text).toContain("Nguồn tài liệu D134, trang 1");
    expect(text).not.toContain("**");
    expect(text).not.toContain("[SRC:");
    expect(text).not.toContain("---");
  });
});
