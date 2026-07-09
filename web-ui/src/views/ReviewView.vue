<script setup lang="ts">
import ResourcePage from "@/components/ResourcePage.vue";
import { apiSend, apiGet } from "@/api/client";
import type { ApiRow, RowAction } from "@/types";

async function load(): Promise<ApiRow[]> {
  const data = await apiGet<{ documents: ApiRow[] }>("/api/documents/pending-review");
  return data.documents ?? [];
}

// Danh sach cho duyet lay theo trang thai cua JOB (IngestionJobs.Status = 'pending_review'),
// nen JobID luon co, con DocID den tu LEFT JOIN sang TaiLieu -> co the NULL.
// De dong bo, moi thao tac phai: (1) cap nhat TaiLieu neu co DocID,
// (2) LUON chuyen trang thai JOB de dong bien mat khoi hang cho.
function pickId(row: ApiRow, name: string): number | null {
  const keys = Object.keys(row);
  const target = name.toLowerCase();
  const hit =
    keys.find((k) => k.toLowerCase() === target) ||
    keys.find((k) => k.toLowerCase().includes(target));
  if (!hit) return null;
  const value = Number(row[hit]);
  return Number.isFinite(value) && value > 0 ? value : null;
}

function jobId(row: ApiRow): number | null {
  return pickId(row, "jobid");
}

function docId(row: ApiRow): number | null {
  return pickId(row, "docid");
}

type ReviewStep = { path: string; method: "POST" | "DELETE"; body?: unknown };

async function reviewAction(
  row: ApiRow,
  opts: {
    doc?: (docId: number) => ReviewStep;
    job: (jobId: number) => ReviewStep;
    requireDoc?: boolean;
  },
): Promise<void> {
  const doc = docId(row);
  const job = jobId(row);

  if (opts.requireDoc && doc === null) {
    throw new Error(
      "Chua tao duoc tai lieu (DocID trong) nen khong the xuat ban. Hay dung 'Tu choi' hoac 'Xoa' de go job khoi hang cho.",
    );
  }

  // Buoc 1: cap nhat TaiLieu (chi khi co DocID). Neu that bai -> dung, khong dong JOB.
  if (opts.doc && doc !== null) {
    const step = opts.doc(doc);
    await apiSend(step.path, step.method, step.body);
  }

  // Buoc 2: chuyen trang thai JOB de dong bien mat khoi danh sach cho duyet.
  if (job === null) {
    throw new Error("Khong tim thay JobID cua dong nay.");
  }
  const jobStep = opts.job(job);
  await apiSend(jobStep.path, jobStep.method, jobStep.body);
}

const rowActions: RowAction[] = [
  {
    label: "Xuất bản (version)",
    run: (r) =>
      reviewAction(r, {
        requireDoc: true,
        doc: (id) => ({ path: `/api/documents/${id}/publish-new-version`, method: "POST" }),
        job: (id) => ({ path: `/api/ingestion/jobs/${id}/publish`, method: "POST" }),
      }),
  },
  {
    label: "Xuất bản (variant)",
    run: (r) =>
      reviewAction(r, {
        requireDoc: true,
        doc: (id) => ({ path: `/api/documents/${id}/publish-new-variant`, method: "POST" }),
        job: (id) => ({ path: `/api/ingestion/jobs/${id}/publish`, method: "POST" }),
      }),
  },
  {
    label: "Xuất bản (độc lập)",
    run: (r) =>
      reviewAction(r, {
        requireDoc: true,
        doc: (id) => ({ path: `/api/documents/${id}/publish-standalone`, method: "POST" }),
        job: (id) => ({ path: `/api/ingestion/jobs/${id}/publish`, method: "POST" }),
      }),
  },
  {
    label: "Từ chối",
    severity: "warning",
    confirm: "Từ chối tài liệu này?",
    run: (r) =>
      reviewAction(r, {
        doc: (id) => ({ path: `/api/documents/${id}/reject`, method: "POST" }),
        job: (id) => ({
          path: `/api/ingestion/jobs/${id}/reject`,
          method: "POST",
          body: { reason: "Từ chối từ trang duyệt tài liệu" },
        }),
      }),
  },
  {
    label: "Lưu trữ",
    run: (r) =>
      reviewAction(r, {
        doc: (id) => ({ path: `/api/documents/${id}/archive`, method: "POST" }),
        // Khong co trang thai JOB rieng cho 'archived'; danh dau 'published'
        // de job roi hang cho (tai lieu da mang trang thai archived).
        job: (id) => ({ path: `/api/ingestion/jobs/${id}/publish`, method: "POST" }),
      }),
  },
  {
    label: "Xoá",
    severity: "danger",
    confirm: "Xoá vĩnh viễn?",
    run: (r) =>
      reviewAction(r, {
        doc: (id) => ({ path: `/api/documents/${id}`, method: "DELETE" }),
        job: (id) => ({ path: `/api/ingestion/jobs/${id}`, method: "DELETE" }),
      }),
  },
];
</script>

<template>
  <ResourcePage
    title="Duyệt tài liệu"
    eyebrow="Review"
    description="Tài liệu đang chờ duyệt. Chọn hình thức xuất bản hoặc từ chối."
    :load="load"
    :row-actions="rowActions"
  />
</template>
