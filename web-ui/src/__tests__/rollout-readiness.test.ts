import { describe, expect, it } from "vitest";
import {
  filterRolloutReadiness,
  MANAGED_ROLLOUT_STATUSES,
  normalizeRolloutReadiness,
} from "@/utils/rollout-readiness";

describe("rollout readiness presentation", () => {
  it("does not expose the Wave 1 bootstrap-only pilot state as an operator transition", () => {
    expect(MANAGED_ROLLOUT_STATUSES).toEqual(["planned", "dark_launch", "active", "blocked"]);
    expect(MANAGED_ROLLOUT_STATUSES).not.toContain("pilot");
  });
  it("uses the backend missing prerequisite list and servable corpus count", () => {
    const row = normalizeRolloutReadiness({
      department_code: "Warehouse",
      wave_number: 2,
      rollout_status: "planned",
      servable_document_count: 4,
      missing_prerequisites: ["knowledge_owner", "evaluation_gate"],
    });

    expect(row.servable_document_count).toBe(4);
    expect(row.missing_prerequisites_display).toBe("Knowledge Owner, Evaluation gate");
  });

  it("remains compatible with the legacy prerequisites object", () => {
    const row = normalizeRolloutReadiness({
      prerequisites: {
        rollout_plan: true,
        servable_corpus: false,
        evaluation_set: false,
      },
    });

    expect(row.missing_prerequisites).toEqual(["servable_corpus", "evaluation_set"]);
    expect(row.missing_prerequisites_display).toBe("Corpus đang phục vụ, Bộ câu evaluation");
  });

  it("filters the readiness table by wave and status on the client", () => {
    const rows = [
      { department_code: "Technical", wave_number: 1, rollout_status: "pilot" },
      { department_code: "Sales", wave_number: 2, rollout_status: "planned" },
      { department_code: "Planning", wave_number: 2, rollout_status: "blocked" },
    ];

    expect(filterRolloutReadiness(rows, { wave_number: 2, rollout_status: "planned" }))
      .toEqual([rows[1]]);
  });
});
