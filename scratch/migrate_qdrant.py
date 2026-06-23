import os
import sys
from dotenv import load_dotenv
from qdrant_client import QdrantClient

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()
qdrant_url = os.getenv("QDRANT_URL", "")
qdrant_api_key = os.getenv("QDRANT_API_KEY", "")

client = QdrantClient(
    url=qdrant_url,
    api_key=qdrant_api_key,
    timeout=120,
)

collection_name = "TaiLieuKyThuat_v2"

# Get all points
from qdrant_client.http import models

scroll_filter = models.Filter()
offset = None
while True:
    points, offset = client.scroll(
        collection_name=collection_name,
        scroll_filter=scroll_filter,
        limit=1000,
        with_payload=True,
        with_vectors=False,
    )
    
    if not points:
        break
        
    for p in points:
        if "doc_status" not in p.payload.get("metadata", {}):
            new_metadata = p.payload.get("metadata", {})
            new_metadata["doc_status"] = "published"
            client.set_payload(
                collection_name=collection_name,
                payload={"metadata": new_metadata},
                points=[p.id],
            )
            
    print(f"Updated {len(points)} points...")
    if offset is None:
        break

print("Migration Qdrant successful.")
