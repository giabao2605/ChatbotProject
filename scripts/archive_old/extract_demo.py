import os
import sys
import io
import google.generativeai as genai
from dotenv import load_dotenv
from logger_config import logger
from pdf_processor import process_and_ingest_pdf

# Fix encoding trên Windows
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

load_dotenv()

# Cấu hình Gemini API
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
if GOOGLE_API_KEY and GOOGLE_API_KEY != "DIEN_KEY_CUA_BAN_VAO_DAY":
    genai.configure(api_key=GOOGLE_API_KEY)
    vision_model = genai.GenerativeModel('gemini-2.5-flash')
else:
    vision_model = None
    logger.warning("Chưa cấu hình GOOGLE_API_KEY hợp lệ trong file .env, sẽ bỏ qua bước nhận diện ảnh bằng Gemini!")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "Data_Goc")

def console_progress(msg):
    print(f"  {msg}")

print(f"BẮT ĐẦU CHẠY SCRIPT NẠP DỮ LIỆU TOÀN BỘ (Dùng pdf_processor chung)...")
print(f"📂 Đang quét thư mục: {DATA_DIR}")

total_chunks = 0
so_file_thanh_cong = 0

for thu_muc in sorted(os.listdir(DATA_DIR)):
    thu_muc_path = os.path.join(DATA_DIR, thu_muc)
    if not os.path.isdir(thu_muc_path):
        continue
    
    print(f"\n=== ĐANG XỬ LÝ THƯ MỤC: {thu_muc} ===")
    
    for ten_file in sorted(os.listdir(thu_muc_path)):
        if not ten_file.endswith(".pdf"):
            continue
        
        pdf_path = os.path.join(thu_muc_path, ten_file)
        print(f"\n  File: {ten_file}")
        
        report = process_and_ingest_pdf(pdf_path, ten_file, thu_muc, vision_model, progress_callback=console_progress)
        
        if report["status"] == "success":
            print(f"  Thành công! Đã nạp {report['total_chunks']} chunks. Mất {report['time_taken']}s.")
            total_chunks += report["total_chunks"]
            so_file_thanh_cong += 1
        else:
            print(f"  Lỗi khi xử lý file {ten_file}: {report['message']}")

print(f"\nHOÀN TẤT NẠP DỮ LIỆU! Tổng: {total_chunks} chunks từ {so_file_thanh_cong} file.")