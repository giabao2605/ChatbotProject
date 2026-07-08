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
  { label: "user", value: "user" },
  { label: "uploader", value: "uploader" },
  { label: "reviewer", value: "reviewer" },
  { label: "admin", value: "admin" },
];

// Resolve a value from a row by trying candidate keys (exact match first, then
// case-insensitive contains). Useful when DB column names vary.
export function pickField(row: ApiRow, candidates: string[]): unknown {
  const keys = Object.keys(row);
  for (const cand of candidates) {
    const exact = keys.find((k) => k.toLowerCase() === cand.toLowerCase());
    if (exact) return row[exact];
  }
  for (const cand of candidates) {
    const fuzzy = keys.find((k) => k.toLowerCase().includes(cand.toLowerCase()));
    if (fuzzy) return row[fuzzy];
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
