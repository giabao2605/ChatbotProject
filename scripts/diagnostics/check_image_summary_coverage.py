import os
import sys
import json
from collections import defaultdict
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from db_logic import engine
from qdrant_client import QdrantClient

def check_coverage():
    from dotenv import load_dotenv
    load_dotenv()
    
    q_url = os.getenv("QDRANT_URL")
    q_api = os.getenv("QDRANT_API_KEY")
    client = QdrantClient(url=q_url, api_key=q_api)
    collection = "TaiLieuKyThuat_v2"
    
    print("Checking SQL Database...")
    with engine.connect() as conn:
        res = conn.execute(text("SELECT COUNT(*) FROM TaiLieu")).scalar()
        total_docs = res
        
        # Ingestion failures
        failed_pages = conn.execute(text("SELECT COUNT(*) FROM IngestionJobs WHERE Status = 'failed'")).scalar()
        
    print("Checking Qdrant...")
    all_points = []
    next_offset = None
    
    try:
        while True:
            points, next_offset = client.scroll(
                collection_name=collection,
                scroll_filter=None,
                limit=1000,
                offset=next_offset,
                with_payload=True,
                with_vectors=False
            )
            all_points.extend(points)
            if next_offset is None:
                break
    except Exception as e:
        print(f"Error querying Qdrant: {e}")

    docs_with_image_summary = set()
    all_docs = set()
    image_summary_chunks = 0
    uncertain_fields = 0
    
    for p in all_points:
        meta = p.payload.get("metadata", {})
        doc_id = meta.get("doc_id")
        loai = meta.get("loai_du_lieu")
        
        if doc_id:
            all_docs.add(doc_id)
            if loai == "image_summary":
                docs_with_image_summary.add(doc_id)
                image_summary_chunks += 1
                
        # heuristic for uncertain fields
        content = p.payload.get("page_content", "")
        if "Khong ro" in content or "uncertain" in content.lower():
            uncertain_fields += 1

    docs_without = len(all_docs) - len(docs_with_image_summary)

    print("\n--- Coverage Report ---")
    print(f"Total docs (SQL): {total_docs}")
    print(f"Total docs (Qdrant): {len(all_docs)}")
    print(f"Docs with image_summary: {len(docs_with_image_summary)}")
    print(f"Docs without image_summary: {docs_without}")
    print(f"Image summary chunk count: {image_summary_chunks}")
    print(f"OCR failed pages (Ingestion jobs): {failed_pages}")
    print(f"Uncertain fields count (heuristic): {uncertain_fields}")
    
if __name__ == "__main__":
    check_coverage()
