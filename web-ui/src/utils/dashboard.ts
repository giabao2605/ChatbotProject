const DASHBOARD_LABELS: Record<string, string> = {
  total_docs: "Tổng tài liệu",
  effective: "Còn hiệu lực",
  published_docs: "Đã xuất bản",
  expired: "Đã hết hạn",
  expiring_soon: "Sắp hết hạn",
  needs_review: "Cần rà soát",
  pending: "Chờ duyệt",
  publish_blocked: "Publish bị chặn",
  running: "Job đang chạy",
  failed: "Job lỗi",
  today_questions: "Câu hỏi hôm nay",
  recent_questions: "Câu hỏi trong 30 ngày",
  pending_feedback: "Feedback chờ xử lý",
  departments_total: "Tổng phòng ban",
  departments_active: "Phòng ban đã kích hoạt",
  departments_pilot: "Phòng ban pilot",
  departments_planned: "Phòng ban đã lên kế hoạch",
  departments_dark_launch: "Phòng ban dark launch",
  departments_blocked: "Phòng ban bị chặn",
  rollout_percent: "Tiến độ rollout (%)",
};

export function dashboardMetricLabel(key: string): string {
  return DASHBOARD_LABELS[key] ?? key.replace(/_/g, " ");
}

export function dashboardMetricTarget(key: string): string | null {
  if (["effective", "published_docs", "total_docs"].includes(key)) {
    return "/documents?tab=effective";
  }
  if (["expired", "expiring_soon", "needs_review"].includes(key)) {
    return `/documents?tab=${key.replace("_", "-")}`;
  }
  if (["running", "failed"].includes(key)) return "/queue";
  if (["pending", "publish_blocked"].includes(key)) return "/review";
  return null;
}
