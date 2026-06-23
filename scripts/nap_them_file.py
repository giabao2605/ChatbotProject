"""
Script nạp thêm file vào Qdrant từ command line.
Hỗ trợ mọi định dạng: PDF, DOCX, XLSX, CSV, TXT, ảnh...

Cách dùng:
    python scripts/nap_them_file.py To_Han/ban_ve_1.pdf To_Son/catalog.xlsx
"""
import os
import sys
import io
from dotenv import load_dotenv

# Fix encoding trên Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Thêm đường dẫn project root để import
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from logger_config import logger
from file_learning import learn_new_file  # Dùng learn_new_file để hỗ trợ multi-format

# BASE_DIR trỏ đúng về project root (không phải scripts/)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "Data_Goc")

def console_progress(msg):
    print(f"  {msg}")

if __name__ == "__main__":
    print("BẮT ĐẦU CHẠY SCRIPT NẠP THÊM DỮ LIỆU...")
    logger.info("Bắt đầu chạy script nap_them_file.py")

    danh_sach_file = sys.argv[1:]
    if not danh_sach_file:
        print("Cách dùng: python scripts/nap_them_file.py <relative_path_1> <relative_path_2> ...")
        print("Ví dụ:     python scripts/nap_them_file.py To_Han/9.3.03843.pdf")
        sys.exit(0)

    print(f"\n📋 Sẽ nạp thêm {len(danh_sach_file)} file:")
    for f in danh_sach_file:
        print(f"   - {f}")
    print()

    total_chunks = 0
    so_file_thanh_cong = 0
    so_file_loi = 0

    for relative_path in danh_sach_file:
        file_path = os.path.join(DATA_DIR, relative_path)
        if not os.path.exists(file_path):
            print(f"Không tìm thấy: {file_path}")
            logger.error(f"Không tìm thấy file: {file_path}")
            so_file_loi += 1
            continue

        thu_muc = os.path.basename(os.path.dirname(file_path)) or "Data_Goc"
        ten_file = os.path.basename(file_path)
        print(f"\n=== ĐANG XỬ LÝ: {ten_file} (Thư mục: {thu_muc}) ===")
        logger.info(f"Đang xử lý file: {ten_file}")

        success, msg, _ = learn_new_file(file_path, ten_file, thu_muc=thu_muc, progress_callback=console_progress)

        if success:
            print(f"  ✅ Thành công! {msg}")
            logger.info(f"Nạp thành công {ten_file}: {msg}")
            so_file_thanh_cong += 1
        else:
            print(f"  ❌ Lỗi: {msg}")
            logger.error(f"Lỗi nạp file {ten_file}: {msg}")
            so_file_loi += 1

    print(f"\n{'='*50}")
    print(f"HOÀN TẤT! Thành công: {so_file_thanh_cong} | Lỗi: {so_file_loi}")