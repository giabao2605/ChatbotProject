import type { ApiRow } from "@/types";

// `pilot` is a bootstrap state reserved for the fixed Wave 1 cohort. Operators
// may manage the normal lifecycle below, while the backend remains authoritative
// for transition validation.
export const MANAGED_ROLLOUT_STATUSES = ["planned", "dark_launch", "active", "blocked"] as const;

const PREREQUISITE_LABELS: Record<string, string> = {
  rollout_plan: "Rollout plan",
  knowledge_owner: "Knowledge Owner",
  knowledge_approver: "Knowledge Approver",
  taxonomy: "Taxonomy",
  governance_active: "Governance đang hoạt động",
  domain_profile_active: "Domain profile đang hoạt động",
  domain_profile_valid: "Domain profile hợp lệ",
  site_backfill: "Backfill site",
  servable_corpus: "Corpus đang phục vụ",
  evaluation_set: "Bộ câu evaluation",
  evaluation_gate: "Evaluation gate",
};

function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => String(item ?? "").trim()).filter(Boolean);
}

export function missingPrerequisiteKeys(row: ApiRow): string[] {
  const explicit = stringList(row.missing_prerequisites);
  if (explicit.length) return explicit;

  const prerequisites = row.prerequisites;
  if (!prerequisites || typeof prerequisites !== "object" || Array.isArray(prerequisites)) return [];
  return Object.entries(prerequisites as Record<string, unknown>)
    .filter(([, passed]) => passed !== true)
    .map(([key]) => key);
}

export function prerequisiteLabel(key: string): string {
  return PREREQUISITE_LABELS[key] ?? key.replace(/_/g, " ");
}

export function normalizeRolloutReadiness(row: ApiRow): ApiRow {
  const missing = missingPrerequisiteKeys(row);
  return {
    ...row,
    servable_document_count: Number(row.servable_document_count ?? 0),
    missing_prerequisites: missing,
    missing_prerequisites_display: missing.length
      ? missing.map(prerequisiteLabel).join(", ")
      : "Không còn điều kiện thiếu",
  };
}

export function filterRolloutReadiness(
  rows: ApiRow[],
  filters: Record<string, unknown>,
): ApiRow[] {
  const wave = String(filters.wave_number ?? "").trim();
  const status = String(filters.rollout_status ?? "").trim().toLowerCase();
  return rows.filter((row) => {
    if (wave && String(row.wave_number ?? "") !== wave) return false;
    if (status && String(row.rollout_status ?? "").toLowerCase() !== status) return false;
    return true;
  });
}
