from dotenv import load_dotenv
import os
from qdrant_client import QdrantClient
from qdrant_client.http import models

load_dotenv()
try:
    qdrant_url = os.getenv("QDRANT_URL")
    qdrant_api_key = os.getenv("QDRANT_API_KEY")
    client = QdrantClient(
        url=qdrant_url,
        api_key=qdrant_api_key,
        timeout=120,
    )
    collections = client.get_collections().collections
    print("Found collections:", [c.name for c in collections])
    for c in collections:
        info = client.get_collection(c.name)
        print(f"Collection {c.name}: {info.points_count} points")
        
    # Attempt to explicitly check TaiLieuKyThuat_v2
    try:
        info = client.get_collection("TaiLieuKyThuat_v2")
        print("Explicit TaiLieuKyThuat_v2 points:", info.points_count)
    except Exception as e:
        print("Explicit TaiLieuKyThuat_v2 missing:", e)

except Exception as e:
    print("Qdrant Error:", e)
