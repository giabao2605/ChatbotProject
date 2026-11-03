import type { Citation } from '@/types';

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderInline(value: string, citations: Citation[] = []): string {
  return escapeHtml(value)
    .replace(/\[SRC:\s*(D\d+(?:P\d+|L[a-f0-9]+))\]/gi, (_marker, sourceId: string) => `<span class="citation-marker">${escapeHtml(formatSourceReference(sourceId, citations))}</span>`)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

function isTableSeparator(line: string): boolean {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function splitTableRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderTable(lines: string[], citations: Citation[]): string {
  const header = splitTableRow(lines[0]);
  const rows = lines.slice(2).map(splitTableRow);
  const colCount = header.length;
  const body = rows
    .map((row) => {
      const cells = Array.from({ length: colCount }, (_item, index) => row[index] ?? "");
      return `<tr>${cells.map((cell) => `<td>${renderInline(cell, citations)}</td>`).join("")}</tr>`;
    })
    .join("");

  return [
    '<div class="md-table-wrap">',
    "<table>",
    `<thead><tr>${header.map((cell) => `<th>${renderInline(cell, citations)}</th>`).join("")}</tr></thead>`,
    `<tbody>${body}</tbody>`,
    "</table>",
    "</div>",
  ].join("");
}

function flushParagraph(parts: string[], paragraph: string[], citations: Citation[]) {
  if (!paragraph.length) return;
  parts.push(`<p>${paragraph.map(line => renderInline(line, citations)).join("<br>")}</p>`);
  paragraph.length = 0;
}

export function renderMarkdown(value: string, citations: Citation[] = []): string {
  const lines = (value || "").replace(/\r\n/g, "\n").split("\n");
  const parts: string[] = [];
  const paragraph: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      flushParagraph(parts, paragraph, citations);
      i += 1;
      continue;
    }

    if (trimmed.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      flushParagraph(parts, paragraph, citations);
      const tableLines = [line, lines[i + 1]];
      i += 2;
      while (i < lines.length && lines[i].trim().includes("|") && lines[i].trim()) {
        tableLines.push(lines[i]);
        i += 1;
      }
      parts.push(renderTable(tableLines, citations));
      continue;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushParagraph(parts, paragraph, citations);
      const level = heading[1].length + 2;
      parts.push(`<h${level}>${renderInline(heading[2], citations)}</h${level}>`);
      i += 1;
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      flushParagraph(parts, paragraph, citations);
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*]\s+/, ""));
        i += 1;
      }
      parts.push(`<ul>${items.map((item) => `<li>${renderInline(item, citations)}</li>`).join("")}</ul>`);
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const start = Number.parseInt(trimmed, 10);
      flushParagraph(parts, paragraph, citations);
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ""));
        i += 1;
      }
      parts.push(`<ol${start === 1 ? "" : ` start="${start}"`}>${items.map((item) => `<li>${renderInline(item, citations)}</li>`).join("")}</ol>`);
      continue;
    }

    paragraph.push(line);
    i += 1;
  }

  flushParagraph(parts, paragraph, citations);
  return parts.join("");
}

function formatSourceReference(value: string, citations: Citation[] = []): string {
  const citation = citations.find(item => item.source_id?.toUpperCase() === value.toUpperCase());
  if (citation) {
    const locator = citation.locator;
    const location = locator ? [locator.page ? `trang ${locator.page}` : '',
      locator.sheet ? `Sheet ${locator.sheet}` : '', locator.cell ? `Ô ${locator.cell}` : '',
      locator.row_key ? `Dòng ${locator.row_key}` : ''].filter(Boolean) :
      citation.page_no ? [`trang ${citation.page_no}`] : [];
    return [citation.file_name || `Nguồn tài liệu D${citation.doc_id}`, ...location].join(' · ');
  }
  const page = /^D(\d+)P(\d+)$/i.exec(value.trim());
  if (page) return `Nguồn tài liệu D${page[1]}, trang ${page[2]}`;
  const document = /^D(\d+)L[a-f0-9]+$/i.exec(value.trim());
  return document ? `Nguồn tài liệu D${document[1]}` : value;
}

/** Turn the legacy reference appendix into plain, readable source lines. */
export function formatSourceText(value: string, citations: Citation[] = []): string {
  return (value || "")
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line && !/^[-=]{3,}$/.test(line))
    .map((line) => line
      .replace(/^#{1,6}\s+/, "")
      .replace(/^[-*]\s+/, "")
      .replace(/\*\*|__|`/g, "")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/\[SRC:\s*(D\d+(?:P\d+|L[a-f0-9]+))\]/gi, (_marker, sourceId: string) => formatSourceReference(sourceId, citations))
      .replace(/\b(?:source[_ ]?id)\s*[:#]?\s*(D\d+(?:P\d+|L[a-f0-9]+))/gi, (_label, sourceId: string) => formatSourceReference(sourceId, citations)),
    )
    .filter(Boolean)
    .join("\n");
}
