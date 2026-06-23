import os
import sys
import time
import traceback

# Thêm thư mục gốc vào sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_logic import get_pending_job, update_ingestion_job, mark_job_failed, mark_job_waiting_quota, write_audit_log, update_ingestion_report
from file_learning import learn_new_file
from logger_config import logger

def run_worker():
    logger.info("Khởi động Ingestion Worker chạy ngầm...")


    print("Ingestion Worker đã sẵn sàng. Đang chờ file mới...")
    
    while True:
        try:
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
            
            logger.info(f"Worker bắt đầu xử lý JobID {job_id}: {file_name}")
            print(f"\n[{time.strftime('%H:%M:%S')}] Đang xử lý: {file_name}")
            
            # 1.5. Phân loại AI
            try:
                from document_classifier import classify_document
                import json
                from db_logic import engine
                from sqlalchemy import text
                
                print(f"[{time.strftime('%H:%M:%S')}] Đang phân loại bằng AI...")
                cls_res = classify_document(file_path, file_name)
                
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
            success, message, report = learn_new_file(
                file_path=file_path,
                ten_file=file_name,
                thu_muc=thu_muc,
                progress_callback=progress_handler
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
                else:
                    mark_job_failed(job_id, error_message=message + " (Failed quality gate: blocked)")
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
    sys.stdout.reconfigure(encoding='utf-8')
    run_worker()
