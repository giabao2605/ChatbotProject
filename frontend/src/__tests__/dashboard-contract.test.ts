import { describe, expect, it } from "vitest";
import { dashboardMetricLabel, dashboardMetricTarget } from "@/utils/dashboard";

describe("role dashboard public contract", () => {
  it("presents the exact metric keys returned by the backend", () => {
    expect(dashboardMetricLabel("running")).toBe("Job đang chạy");
    expect(dashboardMetricLabel("failed")).toBe("Job lỗi");
    expect(dashboardMetricLabel("pending")).toBe("Chờ duyệt");
    expect(dashboardMetricLabel("today_questions")).toBe("Câu hỏi hôm nay");
    expect(dashboardMetricLabel("departments_planned")).toBe("Phòng ban đã lên kế hoạch");
  });

  it("routes actionable backend metrics to the matching operational page", () => {
    expect(dashboardMetricTarget("running")).toBe("/queue");
    expect(dashboardMetricTarget("failed")).toBe("/queue");
    expect(dashboardMetricTarget("pending")).toBe("/review");
    expect(dashboardMetricTarget("publish_blocked")).toBe("/review");
    expect(dashboardMetricTarget("expiring_soon")).toBe("/documents?tab=expiring-soon");
    expect(dashboardMetricTarget("departments_planned")).toBeNull();
  });
});
