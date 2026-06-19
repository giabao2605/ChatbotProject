import os
import sys
import time

# Thêm thư mục gốc vào sys.path để import được các module từ thư mục ngoài
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models
from logger_config import logger

def create_indexes():
    """
    Tạo Payload Index cho Qdrant để tăng tốc độ filter/tìm kiếm
    khi dữ liệu quy mô lớn (scale up).
    """
    # Load biến môi trường
    load_dotenv()
    
    qdrant_url = os.getenv("QDRANT_URL", "")
    qdrant_api_key = os.getenv("QDRANT_API_KEY", "")
    
    if not qdrant_url or not qdrant_api_key:
        logger.error("Thiếu thiết lập QDRANT_URL hoặc QDRANT_API_KEY trong file .env")
        return

    logger.info(f"Đang kết nối tới Qdrant tại: {qdrant_url} ...")
    try:
        client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        
        collection_name = "TaiLieuKyThuat_v2"
        
        # Kiểm tra collection tồn tại
        if not client.collection_exists(collection_name):
            logger.error(f"Collection '{collection_name}' không tồn tại. Vui lòng chạy ứng dụng chính trước để khởi tạo.")
            return

        # Định nghĩa các index cần tạo
        REQUIRED_INDEXES = {
            "metadata.ma_doi_tuong": models.PayloadSchemaType.KEYWORD,
            "metadata.file_goc": models.PayloadSchemaType.KEYWORD,
            "metadata.phong_ban_quyen": models.PayloadSchemaType.KEYWORD,
            "metadata.loai_du_lieu": models.PayloadSchemaType.KEYWORD,
            "metadata.doc_id": models.PayloadSchemaType.INTEGER,
            "metadata.family_id": models.PayloadSchemaType.INTEGER,
            "metadata.base_code": models.PayloadSchemaType.KEYWORD,
            "metadata.version_no": models.PayloadSchemaType.INTEGER,
            "metadata.version_label": models.PayloadSchemaType.KEYWORD,
            "metadata.variant_code": models.PayloadSchemaType.KEYWORD,
            "metadata.lifecycle_status": models.PayloadSchemaType.KEYWORD,
            "metadata.review_status": models.PayloadSchemaType.KEYWORD,
            "metadata.is_current": models.PayloadSchemaType.BOOL,
            "metadata.is_archived": models.PayloadSchemaType.BOOL,
        }
        
        # Lấy thông tin schema hiện tại
        info = client.get_collection(collection_name)
        existing_indexes = info.payload_schema or {}
        
        logger.info(f"Schema hiện tại có {len(existing_indexes)} index(es).")
        
        # Tạo index nếu chưa có
        created_count = 0
        for field_name, field_schema in REQUIRED_INDEXES.items():
            if field_name not in existing_indexes:
                logger.info(f"Đang tạo Payload Index cho trường '{field_name}' ...")
                client.create_payload_index(
                    collection_name=collection_name,
                    field_name=field_name,
                    field_schema=field_schema,
                    wait=True,
                )
                created_count += 1
                logger.info(f"✓ Tạo index '{field_name}' thành công.")
            else:
                logger.info(f"Trường '{field_name}' đã được index.")
                
        logger.info(f"Đã tạo mới {created_count} index(es). Tối ưu hóa Qdrant hoàn tất.")
        
    except Exception as e:
        logger.error(f"Có lỗi xảy ra khi kết nối/tạo index Qdrant: {e}")

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    create_indexes()
