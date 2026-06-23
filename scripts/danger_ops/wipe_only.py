import os
import shutil
import sys
from sqlalchemy import text
# pyrefly: ignore [missing-import]
from qdrant_client import QdrantClient
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
load_dotenv()

print("1. Dang xoa collection Qdrant 'TaiLieuKyThuat_v2'...")
try:
    qdrant_url = os.getenv("QDRANT_URL", "")
    qdrant_api_key = os.getenv("QDRANT_API_KEY", "")
    client = QdrantClient(
        url=qdrant_url,
        api_key=qdrant_api_key,
        timeout=120,
    )
    client.delete_collection(collection_name="TaiLieuKyThuat_v2")
    print(" -> Da xoa Qdrant collection thanh cong.")
except Exception as e:
    print(f" -> Loi hoac collection khong ton tai: {e}")

print("2. Dang xoa du lieu SQL Server (TaiLieu, TaiLieuKyThuat)...")
from db_logic import engine
try:
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM BangKeVatTu"))
        conn.execute(text("DELETE FROM DocumentPages"))
        conn.execute(text("DELETE FROM TechnicalAttributes"))
        conn.execute(text("DELETE FROM TaiLieuKyThuat"))
        conn.execute(text("DELETE FROM TaiLieu"))
        conn.execute(text("DELETE FROM IngestionJobs"))
    print(" -> Da don dep SQL Server thanh cong.")
except Exception as e:
    print(f" -> Loi xoa SQL Server: {e}")

print("3. Dang xoa file anh trong Data_Anh_Da_Tach...")
base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
anh_dir = os.path.join(base_dir, "Data_Anh_Da_Tach")
if os.path.exists(anh_dir):
    try:
        shutil.rmtree(anh_dir)
        os.makedirs(anh_dir)
        print(" -> Da don dep thu muc Data_Anh_Da_Tach thanh cong.")
    except Exception as e:
        print(f" -> Loi xoa Data_Anh_Da_Tach: {e}")
else:
    os.makedirs(anh_dir)

print("\nHOAN TAT XOA DU LIEU!")
