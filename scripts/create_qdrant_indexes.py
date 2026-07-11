import os
import sys
import time

# Them src vao sys.path khi chay script truc tiep.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models
from mech_chatbot.config.logging import logger
from mech_chatbot.config.settings import QDRANT_COLLECTION

def create_indexes():
    """
    Tạo Payload Index cho Qdrant để tăng tốc độ filter/tìm kiếm
    khi dữ liệu quy mô lớn (scale up).
    """
    load_dotenv()

    qdrant_url = os.getenv("QDRANT_URL", "")
    qdrant_api_key = os.getenv("QDRANT_API_KEY", "")

    if not qdrant_url or not qdrant_api_key:
        logger.error("Thiếu thiết lập QDRANT_URL hoặc QDRANT_API_KEY trong file .env")
        return False

    logger.info(f"Đang kết nối tới Qdrant tại: {qdrant_url} ...")
    try:
        client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=120)

        collection_name = QDRANT_COLLECTION

        if not client.collection_exists(collection_name):
            logger.error(f"Collection '{collection_name}' không tồn tại. Vui lòng chạy ứng dụng chính trước để khởi tạo.")
            return False

        REQUIRED_INDEXES = {
            "metadata.ma_doi_tuong": models.PayloadSchemaType.KEYWORD,
            "metadata.ma_chinh": models.PayloadSchemaType.KEYWORD,
            "metadata.ma_btp": models.PayloadSchemaType.KEYWORD,
            "metadata.ma_vat_tu": models.PayloadSchemaType.KEYWORD,
            "metadata.ma_lien_quan": models.PayloadSchemaType.KEYWORD,
            "metadata.file_goc": models.PayloadSchemaType.KEYWORD,
            "metadata.thu_muc": models.PayloadSchemaType.KEYWORD,
            "metadata.phong_ban_quyen": models.PayloadSchemaType.KEYWORD,
            "metadata.security_level": models.PayloadSchemaType.KEYWORD,
            "metadata.site": models.PayloadSchemaType.KEYWORD,
            "metadata.domain": models.PayloadSchemaType.KEYWORD,
            "metadata.loai_du_lieu": models.PayloadSchemaType.KEYWORD,
            "metadata.doc_type": models.PayloadSchemaType.KEYWORD,
            "metadata.document_type": models.PayloadSchemaType.KEYWORD,
            "metadata.document_type_family": models.PayloadSchemaType.KEYWORD,
            "metadata.doc_number": models.PayloadSchemaType.KEYWORD,
            "metadata.doc_status": models.PayloadSchemaType.KEYWORD,
            "metadata.doc_id": models.PayloadSchemaType.INTEGER,
            "metadata.family_id": models.PayloadSchemaType.INTEGER,
            "metadata.base_code": models.PayloadSchemaType.KEYWORD,
            "metadata.version_no": models.PayloadSchemaType.INTEGER,
            "metadata.version_label": models.PayloadSchemaType.KEYWORD,
            "metadata.variant_code": models.PayloadSchemaType.KEYWORD,
            "metadata.lifecycle_status": models.PayloadSchemaType.KEYWORD,
            "metadata.review_status": models.PayloadSchemaType.KEYWORD,
            "metadata.is_current": models.PayloadSchemaType.BOOL,
            "metadata.effective_status": models.PayloadSchemaType.KEYWORD,
            "metadata.is_archived": models.PayloadSchemaType.BOOL,
            "metadata.servable": models.PayloadSchemaType.BOOL,
            "metadata.publication_state": models.PayloadSchemaType.KEYWORD,
            "metadata.publication_version": models.PayloadSchemaType.INTEGER,
            "metadata.serving_epoch": models.PayloadSchemaType.INTEGER,
            "metadata.taxonomy_version": models.PayloadSchemaType.KEYWORD,
            "metadata.parent_applicable": models.PayloadSchemaType.BOOL,
            "metadata.parent_context_enabled": models.PayloadSchemaType.BOOL,
            "metadata.parent_page": models.PayloadSchemaType.INTEGER,
            "metadata.parent_section": models.PayloadSchemaType.KEYWORD,
            "metadata.external_processing_policy": models.PayloadSchemaType.KEYWORD,
        }

        info = client.get_collection(collection_name)
        existing_indexes = info.payload_schema or {}
        logger.info(f"Schema hiện tại có {len(existing_indexes)} index(es).")

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
                logger.info(f"Tạo index '{field_name}' thành công.")
            else:
                logger.info(f"Trường '{field_name}' đã được index.")

        logger.info(f"Đã tạo mới {created_count} index(es). Tối ưu hóa Qdrant hoàn tất.")
        return True

    except Exception as e:
        logger.error(f"Có lỗi xảy ra khi kết nối/tạo index Qdrant: {e}")
        return False

if __name__ == "__main__":
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(0 if create_indexes() else 1)
