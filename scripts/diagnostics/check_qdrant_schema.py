import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

# Import rag_logic se trigger viec ensure_schema
from rag_logic import client

try:
    info = client.get_collection("TaiLieuKyThuat_v2")
    print("Schema hien tai tren Qdrant Cloud sau khi load rag_logic:")
    print(info.payload_schema)
except Exception as e:
    print(f"Loi: {e}")
