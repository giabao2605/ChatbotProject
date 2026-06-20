import os
import shutil
import glob
from sqlalchemy import text
import sys
from dotenv import load_dotenv

load_dotenv()

# Thêm đường dẫn gốc để import db_logic
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_logic import engine

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def reset_all_data():
    print("BẮT ĐẦU XÓA TOÀN BỘ DỮ LIỆU...")

    # 1. Xóa ảnh trong Data_Anh_Da_Tach
    anh_dir = os.path.join(BASE_DIR, "Data_Anh_Da_Tach")
    if os.path.exists(anh_dir):
        files = glob.glob(os.path.join(anh_dir, "*"))
        for f in files:
            try:
                os.remove(f)
            except Exception as e:
                print(f"Không thể xóa {f}: {e}")
        print(f"Đã xóa toàn bộ ảnh trong {anh_dir}")

    # 2. Xóa các file PDF user đã tải lên qua UI (nếu có)
    tu_hoc_dir = os.path.join(BASE_DIR, "Data_Goc", "Tu_Hoc")
    if os.path.exists(tu_hoc_dir):
        files = glob.glob(os.path.join(tu_hoc_dir, "*.pdf"))
        for f in files:
            try:
                os.remove(f)
            except Exception as e:
                pass
        print(f"Đã xóa file tự học trong {tu_hoc_dir}")

    # 3. Xóa SQL Database tables (Xóa data)
    try:
        with engine.begin() as conn:
            # Xóa data thay vì drop table để tránh lỗi mất schema
            conn.execute(text("DELETE FROM TaiLieuKyThuat"))
            conn.execute(text("DELETE FROM TaiLieu"))
            conn.execute(text("DELETE FROM LichSuChat"))
            
            # Reset identity (nếu cần thiết, SQL Server dùng DBCC CHECKIDENT)
            try:
                conn.execute(text("DBCC CHECKIDENT ('TaiLieuKyThuat', RESEED, 0)"))
                conn.execute(text("DBCC CHECKIDENT ('TaiLieu', RESEED, 0)"))
                conn.execute(text("DBCC CHECKIDENT ('LichSuChat', RESEED, 0)"))
            except:
                pass
        print("Đã xóa trắng dữ liệu trong SQL Server.")
    except Exception as e:
        print(f"Lỗi khi xóa SQL Server: {e}")

    # 4. Xóa Qdrant DB trên Cloud
    print("4. Đang xóa collection Qdrant 'TaiLieuKyThuat_v2' trên Cloud...")
    try:
        from qdrant_client import QdrantClient
        qdrant_url = os.getenv("QDRANT_URL", "")
        qdrant_api_key = os.getenv("QDRANT_API_KEY", "")
        if qdrant_url and qdrant_api_key:
            client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
            client.delete_collection(collection_name="TaiLieuKyThuat_v2")
            print("Đã xóa Qdrant collection thành công.")
        else:
            print("Không tìm thấy QDRANT_URL hoặc QDRANT_API_KEY trong .env.")
    except Exception as e:
        print(f"Lỗi khi xóa Qdrant collection: {e}")

    print("\nHOÀN TẤT XÓA DỮ LIỆU! Hệ thống đã trống không.")
    return True

if __name__ == "__main__":
    reset_all_data()
