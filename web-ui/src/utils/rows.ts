// Small helpers for reading fields off normalized rows (dict rows keep named
// keys; positional array rows expose c0..cN).
import type { ApiRow } from "@/types";

export function num(row: ApiRow, key: string): number {
  return Number(row[key]);
}

export function str(row: ApiRow, key: string): string {
  const value = row[key];
  return value === null || value === undefined ? "" : String(value);
}

export function bool(row: ApiRow, key: string): boolean {
  return Boolean(row[key]);
}

export const SECURITY_LEVELS = [
  { label: "public", value: "public" },
  { label: "internal", value: "internal" },
  { label: "confidential", value: "confidential" },
];

export const ROLES = [
  { label: "viewer", value: "viewer" },
  { label: "uploader", value: "uploader" },
  { label: "reviewer", value: "reviewer" },
  { label: "admin", value: "admin" },
];

// GD7.6: tach mot khoa cot thanh cac "segment" chu (ca camelCase lan snake_case).
// Vi du: "expected_doc_id" -> ["expected","doc","id"]; "RegQID" -> ["reg","qid"].
function keySegments(key: string): string[] {
  return key
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_\-]+/g, " ")
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
}

// Resolve a value from a row by trying candidate keys.
// GD7.6: uu tien khop CHINH XAC (khong phan biet hoa/thuong). Neu khong co, chi
// khop du phong theo SEGMENT (tu tron ven) thay vi substring, de tranh viec
// ung vien ngan nhu "id" dinh nham cot khac (vd "reg_qid").
export function pickField(row: ApiRow, candidates: string[]): unknown {
  const keys = Object.keys(row);
  // 1) Khop chinh xac ten cot.
  for (const cand of candidates) {
    const exact = keys.find((k) => k.toLowerCase() === cand.toLowerCase());
    if (exact) return row[exact];
  }
  // 2) Du phong: khop theo segment tron ven (khong dung substring).
  for (const cand of candidates) {
    const target = cand.toLowerCase();
    const targetSegs = keySegments(cand);
    const seg = keys.find((k) => {
      const segs = keySegments(k);
      if (segs.includes(target)) return true;
      // Ho tro ung vien nhieu segment (vd "issued_date") khop cot "IssuedDate".
      return targetSegs.length > 1 && targetSegs.every((t) => segs.includes(t));
    });
    if (seg) return row[seg];
  }
  return undefined;
}

export function pickStr(row: ApiRow, candidates: string[]): string {
  const value = pickField(row, candidates);
  return value === null || value === undefined ? "" : String(value);
}

export function pickNum(row: ApiRow, candidates: string[]): number {
  return Number(pickField(row, candidates));
}
