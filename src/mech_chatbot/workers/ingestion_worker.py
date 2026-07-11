import os
import sys
import time
import traceback

# Thêm thư mục gốc vào sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mech_chatbot.db.repository import get_pending_job, update_ingestion_job, mark_job_failed, mark_job_waiting_quota, write_audit_log, update_ingestion_report
from mech_chatbot.ingestion.file_ingestor import learn_new_file
from mech_chatbot.config.logging import logger
from mech_chatbot.llm.external_ai import external_processing_context

def run_worker():
    logger.info("Khởi động Ingestion Worker chạy ngầm...")
    publication_interval = max(
        5, int(os.getenv("PUBLICATION_RECONCILE_INTERVAL_SECONDS", "15"))
    )
    serving_reconcile_interval = max(
        60, int(os.getenv("SERVING_RECONCILE_INTERVAL_SECONDS", "600"))
    )
    last_publication_reconcile = 0.0
    last_serving_reconcile = 0.0

    print("Ingestion Worker đã sẵn sàng. Đang chờ file mới...")
    
    while True:
        try:
            if time.monotonic() - last_publication_reconcile >= publication_interval:
                try:
                    from mech_chatbot.db.repository import reconcile_publications

                    summary = reconcile_publications(limit=10)
                    if summary.get("processed"):
                        logger.info("Publication reconciliation: %s", summary)
                except Exception as reconcile_error:
                    logger.warning(
                        "Publication reconciliation failed: %s", reconcile_error
                    )
                last_publication_reconcile = time.monotonic()

            if time.monotonic() - last_serving_reconcile >= serving_reconcile_interval:
                try:
                    from mech_chatbot.db.repository import reconcile_serving_state

                    serving_summary = reconcile_serving_state(
                        limit=int(os.getenv("SERVING_RECONCILE_BATCH_SIZE", "500")),
                        worker_id="ingestion-worker-serving-reconciler",
                    )
                    if serving_summary.get("failed_doc_ids"):
                        logger.warning("Serving reconciliation has failed docs: %s", serving_summary)
                except Exception as reconcile_error:
                    logger.warning("Serving reconciliation failed: %s", reconcile_error)
                last_serving_reconcile = time.monotonic()

            # 1. Lấy job (và tự động set status = 'extracting' qua CTE OUTPUT)
            job = get_pending_job()
            
            if not job:
                # Không có job, chờ 5s rồi thử lại
                time.sleep(5)
                continue
                
            job_id = job["job_id"]
            file_name = job["ten_file"]
            file_path = job["file_path"]
            thu_muc = job["thu_muc"]
            # GD4: phan loai chon tu form upload (override; None -> suy tu folder)
            domain_override = job.get("domain")
            security_override = job.get("security_level")
            cong_doan_override = job.get("cong_doan")
            site_override = job.get("site")
            phong_ban_override = job.get("phong_ban")
            
            logger.info(f"Worker bắt đầu xử lý JobID {job_id}: {file_name}")
            print(f"\n[{time.strftime('%H:%M:%S')}] Đang xử lý: {file_name}")
            
            # 1.5. Phân loại AI
            try:
                from mech_chatbot.ingestion.document_classifier import classify_document
                import json
                from mech_chatbot.db.repository import engine
                from sqlalchemy import text
                
                print(f"[{time.strftime('%H:%M:%S')}] Đang phân loại bằng AI...")
                with external_processing_context("ingestion-worker", False, f"ingestion_{job_id}"):
                    cls_res = classify_document(file_path, file_name, thu_muc=thu_muc)
                
                with engine.begin() as conn:
                    conn.execute(text("""
                        UPDATE dbo.IngestionJobs 
                        SET ClassificationJson = :j, 
                            ClassificationConfidence = :c, 
                            RequestedAction = :a,
                            Status = 'extracting',
                            ProgressPercent = 20,
                            UpdatedAt = GETDATE()
                        WHERE JobID = :id
                    """), {
                        "j": json.dumps(cls_res, ensure_ascii=False),
                        "c": cls_res.get("confidence", 0.0),
                        "a": cls_res.get("detected_action", "new_document"),
                        "id": job_id
                    })
                write_audit_log("System Worker", "classify_done", "IngestionJobs", job_id, {"cls_res": cls_res})
            except Exception as e:
                logger.error(f"Lỗi khi classify AI: {e}")
            
            # 2. Xử lý file thực tế
            update_ingestion_job(job_id, status="extracting", error_message="Đang bóc tách nội dung...")
            
            def progress_handler(msg):
                print(f"  > {msg}")
                if msg == "__STATUS__:embedding":
                    update_ingestion_job(job_id, status="embedding", error_message="Đang tạo embedding...")

            # Sử dụng learn_new_file của hệ thống
            # scan_sensitive=True: luon do noi dung nhay cam (khong tin folder tuyet doi),
            # ke ca file upload le lan nap hang loat -> tu nang 'confidential' khi can.
            with external_processing_context("ingestion-worker", False, f"ingestion_{job_id}"):
                success, message, report = learn_new_file(
                    file_path=file_path,
                    ten_file=file_name,
                    thu_muc=thu_muc,
                    progress_callback=progress_handler,
                    domain_override=domain_override,
                    security_override=security_override,
                    cong_doan_override=cong_doan_override,
                    site_override=site_override,
                    phong_ban_override=phong_ban_override,
                    scan_sensitive=True,
                )
            
            # 3. Cập nhật kết quả cuối cùng
            if success:
                logger.info(f"Job {job_id} hoàn tất: {message}")
                print(f"[{time.strftime('%H:%M:%S')}] Thành công: {message}")
                
                report_saved = True
                if report:
                    report_saved = update_ingestion_report(job_id, report)
                
                if not report_saved:
                    mark_job_failed(
                        job_id,
                        "Không lưu được ExtractionReport/QualityScore/QualityStatus. Không cho qua quality gate."
                    )
                    continue

                if report and report.get("quality_status") in ["ready_for_review", "needs_review"]:
                    update_ingestion_job(job_id, status="pending_review", error_message="")
                elif report and report.get("quality_status") == "blocked":
                    reasons = ", ".join(report.get("quality_reason_codes", []) or [])
                    mark_job_failed(
                        job_id,
                        "Không đạt quality gate ingest"
                        + (f" ({reasons})" if reasons else ""),
                    )
                else:
                    mark_job_failed(job_id, error_message=message + " (Failed quality gate: khong xac dinh duoc noi dung)")
            else:
                logger.error(f"Job {job_id} thất bại: {message}")
                print(f"[{time.strftime('%H:%M:%S')}] Thất bại: {message}")
                
                if report:
                    update_ingestion_report(job_id, report)
                    
                msg_lower = message.lower()
                if "[quota_exceeded]" in msg_lower or "quota exceeded" in msg_lower or "resource_exhausted" in msg_lower or "free_tier_requests" in msg_lower:
                    mark_job_waiting_quota(job_id, error_message=message)
                else:
                    mark_job_failed(job_id, error_message=message)
                
        except Exception as e:
            logger.error(f"Lỗi không xác định trong Ingestion Worker: {e}\n{traceback.format_exc()}")
            print(f"Lỗi: {e}")
            if 'job_id' in locals():
                e_str_lower = str(e).lower()
                if "[quota_exceeded]" in e_str_lower or "quota exceeded" in e_str_lower or "resource_exhausted" in e_str_lower or "free_tier_requests" in e_str_lower:
                    mark_job_waiting_quota(job_id, error_message=str(e))
                else:
                    mark_job_failed(job_id, error_message=str(e))
            time.sleep(10)

if __name__ == "__main__":
    from mech_chatbot.config.validate import assert_config_valid
    assert_config_valid()
    sys.stdout.reconfigure(encoding='utf-8')
    run_worker()
