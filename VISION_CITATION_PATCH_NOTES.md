# Vision-aware citation and original download patch

## Behavior

- A citation displays a page image only when `DocumentPages.VisionSummary` is non-empty and the rendered image exists.
- Every citation displays `Tải bản gốc`, including text-only and tabular sources.
- Both page preview and original download continue to pass through the existing protected endpoints and `can_access_document()` policy (role/department, security clearance, and site).
- Chat history reconstructs the same `has_vision` state from `DocumentPages`.
- Image files ingested through Vision now create a `DocumentPages` row so they can be previewed later.
- `start_demo_lan.ps1` builds Vue before starting services, preventing stale `web-ui/dist`.

## Re-ingest

- Existing PDFs that already have `DocumentPages.VisionSummary` do not need re-ingest.
- Existing image files ingested before this patch should be re-ingested once to create their `DocumentPages` preview record.
- Word/Excel/CSV/text files do not need re-ingest for the download button.
