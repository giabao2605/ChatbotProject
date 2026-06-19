import os
import sys
import time
import traceback

# Thêm thư mục gốc vào sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db_logic import get_pending_job, update_ingestion_job
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
                        UPDATE IngestionJobs 
                        SET ClassificationJson = :j, ClassificationConfidence = :c, RequestedAction = :a 
                        WHERE JobID = :id
                    """), {
                        "j": json.dumps(cls_res, ensure_ascii=False),
                        "c": cls_res.get("confidence", 0.0),
                        "a": cls_res.get("detected_action", "new_document"),
                        "id": job_id
                    })
            except Exception as e:
                logger.error(f"Lỗi khi classify AI: {e}")
            
            # 2. Xử lý file thực tế
            update_ingestion_job(job_id, status="extracting", error_message="Đang bóc tách PDF...")
            
            # Sử dụng learn_new_file của hệ thống
            success, message = learn_new_file(
                file_path=file_path,
                ten_file=file_name,
                thu_muc=thu_muc,
                progress_callback=lambda msg: print(f"  > {msg}")
            )
            
            # 3. Cập nhật kết quả cuối cùng
            if success:
                logger.info(f"Job {job_id} hoàn tất: {message}")
                print(f"[{time.strftime('%H:%M:%S')}] Thành công: {message}")
                update_ingestion_job(job_id, status="pending_review", error_message="")
            else:
                logger.error(f"Job {job_id} thất bại: {message}")
                print(f"[{time.strftime('%H:%M:%S')}] Thất bại: {message}")
                update_ingestion_job(job_id, status="failed", error_message=message)
                
        except Exception as e:
            logger.error(f"Lỗi không xác định trong Ingestion Worker: {e}\n{traceback.format_exc()}")
            print(f"Lỗi: {e}")
            time.sleep(10)

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    run_worker()
