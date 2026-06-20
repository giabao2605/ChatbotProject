import os
import sys
import time
import io
from PIL import Image
from langchain_core.documents import Document

if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Thêm đường dẫn gốc để import file
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db_logic import engine, text
from rag_logic import vectorstore
from file_learning import vision_model
from logger_config import logger
from pdf_processor import call_gemini_vision

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_DIR = os.path.join(BASE_DIR, "Data_Anh_Da_Tach")

# Danh sách 4 file bị lỗi từ log của bạn
missing_images = [
    ("To_Phoi_9.1.00678-ver02-Model_page1.png", "9.1.00678-ver02-Model.pdf", "To_Phoi"),
    ("To_Son_9.3.03843(975-122)-ver03-Model5_page1.png", "9.3.03843(975-122)-ver03-Model5.pdf", "To_Son"),
    ("To_Son_9.3.03844(975-123)-ver03-Model2_page1.png", "9.3.03844(975-123)-ver03-Model2.pdf", "To_Son"),
    ("To_Tien_Phay_9.3.03843(975-122)-ver03-Model1_page1.png", "9.3.03843(975-122)-ver03-Model1.pdf", "To_Tien_Phay")
]

def get_metadata_from_db(ten_file):
    try:
        with engine.connect() as conn:
            query = text("""
                SELECT b.MaDoiTuong, b.TenSanPham, b.CongDoan, b.SoLuong, b.VatLieu, 
                       b.NguoiLap, b.NgayVe, b.DungSaiDay, b.DungSaiKhac, b.KichThuocTongThe, b.TrangSo, b.LoaiTaiLieu
                FROM TaiLieuKyThuat b
                JOIN TaiLieu t ON b.DocID = t.DocID
                WHERE t.TenFile = :ten_file
            """)
            result = conn.execute(query, {"ten_file": ten_file}).fetchone()
            if result:
                return {
                    "ma_doi_tuong": result[0] or "Không rõ",
                    "ten_sp": result[1] or "Không rõ",
                    "cong_doan": result[2] or "Không rõ",
                    "so_luong": result[3] or "Không rõ",
                    "vat_lieu": result[4] or "Không rõ",
                    "nguoi_lap": result[5] or "Không rõ",
                    "ngay_ve": result[6] or "Không rõ",
                    "dung_sai_day": result[7] or "Không rõ",
                    "dung_sai_khac": result[8] or "Không rõ",
                    "kich_thuoc": result[9] or "Không rõ",
                    "trang_so": result[10] or 1,
                    "loai_tai_lieu": result[11] or "Không rõ"
                }
    except Exception as e:
        print(f"Lỗi truy vấn DB: {e}")
    return None

if __name__ == "__main__":
    print("BẮT ĐẦU NẠP BÙ HÌNH ẢNH BỊ THIẾU...")
    
    if not vision_model:
        print("Chưa cấu hình Gemini API Key!")
        sys.exit(1)

    thanh_cong = 0
    
    for img_name, ten_file_goc, thu_muc in missing_images:
        img_path = os.path.join(IMAGE_DIR, img_name)
        if not os.path.exists(img_path):
            print(f"Không tìm thấy ảnh vật lý: {img_path}")
            continue
            
        print(f"\nĐang xử lý: {img_name}")
        info = get_metadata_from_db(ten_file_goc)
        
        if not info:
            print(f"Không tìm thấy metadata trong DB cho {ten_file_goc}, dùng metadata mặc định.")
            info = {
                "ma_doi_tuong": "Không rõ", "ten_sp": "Không rõ",
                "cong_doan": thu_muc, "so_luong": "Không rõ", "vat_lieu": "Không rõ",
                "nguoi_lap": "Không rõ", "ngay_ve": "Không rõ", "dung_sai_day": "Không rõ",
                "dung_sai_khac": "Không rõ", "kich_thuoc": "Không rõ", "trang_so": 1,
                "loai_tai_lieu": "Bản vẽ gia công"
            }

        # Tạo metadata cho Qdrant
        metadata = {
            "file_goc": ten_file_goc,
            "phong_ban_quyen": thu_muc, 
            "ma_doi_tuong": info["ma_doi_tuong"],
            "loai_tai_lieu": info["loai_tai_lieu"],
            "ten_san_pham": info["ten_sp"],
            "cong_doan": info["cong_doan"],
            "so_luong": info["so_luong"],
            "vat_lieu": info["vat_lieu"],
            "nguoi_lap": info["nguoi_lap"],
            "ngay_ve": info["ngay_ve"],
            "dung_sai_do_day": info["dung_sai_day"],
            "dung_sai_kich_thuoc": info["dung_sai_khac"],
            "kich_thuoc_tong_the": info["kich_thuoc"],
            "trang_so": info["trang_so"],
            "loai_du_lieu": "image_summary"
        }

        try:
            print("   Đang gửi request sang Google Gemini...")
            img_to_analyze = Image.open(img_path)
            prompt = (
                f"Đây là trang số {info['trang_so']} của {info['loai_tai_lieu']}. "
                f"Mã số: {info['ma_doi_tuong']}, Tên: {info['ten_sp']}. "
                f"Hãy mô tả chi tiết những gì bạn thấy trong hình ảnh này: "
                f"hình dáng linh kiện, các góc nhìn mặt cắt, ghi chú kỹ thuật, các thông số/kích thước quan trọng, "
                f"hoặc sơ đồ hướng dẫn công việc nếu có. "
                f"Mô tả của bạn sẽ được dùng để tra cứu RAG, vì vậy hãy trích xuất bất kỳ thông tin nào hữu ích. Trả lời bằng tiếng Việt."
            )
            response = call_gemini_vision(vision_model, prompt, img_to_analyze)
            image_summary = response.text
            
            if image_summary.strip():
                img_summary_content = f"Phân tích hình ảnh tài liệu {ten_file_goc} (Mã: {info['ma_doi_tuong']}):\n{image_summary}"
                doc = Document(page_content=img_summary_content, metadata=metadata)
                
                print("   Đang nạp vào Vector DB (Qdrant)...")
                vectorstore.add_documents([doc])
                print("   Thành công!")
                thanh_cong += 1
                
                # Sleep 15 giây để đảm bảo 100% không bị limit nữa
                print("   Đợi 15s để tránh Rate Limit cho file tiếp theo...")
                time.sleep(15)
            
        except Exception as e:
            print(f"   Lỗi khi phân tích hoặc nạp: {e}")

    print(f"\nHOÀN TẤT! Đã nạp bù thành công {thanh_cong}/{len(missing_images)} ảnh.")
