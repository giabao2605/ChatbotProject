function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderInline(value: string): string {
  return escapeHtml(value)
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

function renderTable(lines: string[]): string {
  const header = splitTableRow(lines[0]);
  const rows = lines.slice(2).map(splitTableRow);
  const colCount = header.length;
  const body = rows
    .map((row) => {
      const cells = Array.from({ length: colCount }, (_item, index) => row[index] ?? "");
      return `<tr>${cells.map((cell) => `<td>${renderInline(cell)}</td>`).join("")}</tr>`;
    })
    .join("");

  return [
    '<div class="md-table-wrap">',
    "<table>",
    `<thead><tr>${header.map((cell) => `<th>${renderInline(cell)}</th>`).join("")}</tr></thead>`,
    `<tbody>${body}</tbody>`,
    "</table>",
    "</div>",
  ].join("");
}

function flushParagraph(parts: string[], paragraph: string[]) {
  if (!paragraph.length) return;
  parts.push(`<p>${paragraph.map(renderInline).join("<br>")}</p>`);
  paragraph.length = 0;
}

export function renderMarkdown(value: string): string {
  const lines = (value || "").replace(/\r\n/g, "\n").split("\n");
  const parts: string[] = [];
  const paragraph: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      flushParagraph(parts, paragraph);
      i += 1;
      continue;
    }

    if (trimmed.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      flushParagraph(parts, paragraph);
      const tableLines = [line, lines[i + 1]];
      i += 2;
      while (i < lines.length && lines[i].trim().includes("|") && lines[i].trim()) {
        tableLines.push(lines[i]);
        i += 1;
      }
      parts.push(renderTable(tableLines));
      continue;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed);
    if (heading) {
      flushParagraph(parts, paragraph);
      const level = heading[1].length + 2;
      parts.push(`<h${level}>${renderInline(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }

    if (/^[-*]\s+/.test(trimmed)) {
      flushParagraph(parts, paragraph);
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*]\s+/, ""));
        i += 1;
      }
      parts.push(`<ul>${items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</ul>`);
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      flushParagraph(parts, paragraph);
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ""));
        i += 1;
      }
      parts.push(`<ol>${items.map((item) => `<li>${renderInline(item)}</li>`).join("")}</ol>`);
      continue;
    }

    paragraph.push(line);
    i += 1;
  }

  flushParagraph(parts, paragraph);
  return parts.join("");
}
