"""
Script Tự Động Test Chatbot RAG — 20 Câu Hỏi, 4 Cấp Độ
Gọi trực tiếp hàm chat_with_rag() và ghi kết quả ra file.
"""
import sys, io, time

# Fix encoding
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from rag_logic import chat_with_rag

# ==========================================
# DANH SÁCH CÂU HỎI TEST
# ==========================================
test_cases = [
    # --- CẤP 1: DỄ (Tra cứu trực tiếp metadata) ---
    {
        "id": 1,
        "level": "🟢 CẤP 1 - DỄ",
        "question": "Mã thành phẩm của cụm hoa văn D40x74mm là gì?",
        "expected": "9.3.03843",
    },
    {
        "id": 2,
        "level": "🟢 CẤP 1 - DỄ",
        "question": "Ai là người lập bản vẽ cho sản phẩm mã 9.3.03843?",
        "expected": "Quang Huy",
    },
    {
        "id": 3,
        "level": "🟢 CẤP 1 - DỄ",
        "question": "Vật liệu chính của khung inox 304 mã 9.3.03844 là gì?",
        "expected": "Inox 304",
    },
    {
        "id": 4,
        "level": "🟢 CẤP 1 - DỄ",
        "question": "Dung sai độ dày vật liệu trong bản vẽ 9.3.03843 là bao nhiêu?",
        "expected": "±0.2 mm",
    },
    {
        "id": 5,
        "level": "🟢 CẤP 1 - DỄ",
        "question": "Số lượng sản xuất của khung inox mã 9.3.03844 là bao nhiêu?",
        "expected": "01",
    },

    # --- CẤP 2: TRUNG BÌNH (Chi tiết kỹ thuật) ---
    {
        "id": 6,
        "level": "🟡 CẤP 2 - TRUNG BÌNH",
        "question": "Bản vẽ tổ hàn của cụm hoa văn 9.3.03843 yêu cầu kiểu hàn gì?",
        "expected": "hàn laser full mối nối",
    },
    {
        "id": 7,
        "level": "🟡 CẤP 2 - TRUNG BÌNH",
        "question": "Mã BTP của cụm hoa văn dành cho tổ nhám trụ là gì?",
        "expected": "8.3.05309.010",
    },
    {
        "id": 8,
        "level": "🟡 CẤP 2 - TRUNG BÌNH",
        "question": "Trong bảng kê vật tư của bản vẽ tổ hàn mã 9.3.03843, có chi tiết Đế Inox 201 mã hàng gì?",
        "expected": "8.3.05306.013",
    },
    {
        "id": 9,
        "level": "🟡 CẤP 2 - TRUNG BÌNH",
        "question": "Kích thước tổng thể của khung inox mã 9.3.03844 là bao nhiêu?",
        "expected": "456x658x1217",
    },
    {
        "id": 10,
        "level": "🟡 CẤP 2 - TRUNG BÌNH",
        "question": "Chiều dài tổng của cụm chân côn inox mã BTP 8.3.06315.009 là bao nhiêu?",
        "expected": "760.6",
    },

    # --- CẤP 3: KHÓ (Suy luận & so sánh chéo) ---
    {
        "id": 11,
        "level": "🔴 CẤP 3 - KHÓ",
        "question": "Quy trình sản xuất cụm hoa văn D40x74mm đi qua những công đoạn nào?",
        "expected": "Tiện, Nhám, Hàn, Sơn, Đóng Gói",
    },
    {
        "id": 12,
        "level": "🔴 CẤP 3 - KHÓ",
        "question": "Dung sai kích thước giữa 2 sản phẩm 9.3.03843 và 9.3.03844 có giống nhau không?",
        "expected": "±0.2 mm và ±0.5 mm",
    },
    {
        "id": 13,
        "level": "🔴 CẤP 3 - KHÓ",
        "question": "Tiêu chuẩn sơn ASTM áp dụng cho sản phẩm mã 9.3.03843 là gì?",
        "expected": "ASTM - B117",
    },
    {
        "id": 14,
        "level": "🔴 CẤP 3 - KHÓ",
        "question": "Bản vẽ tổ hàn của khung inox 9.3.03844 yêu cầu những lưu ý gì khi hàn?",
        "expected": "cồn công nghiệp, không lẹm, vuông ke",
    },
    {
        "id": 15,
        "level": "🔴 CẤP 3 - KHÓ",
        "question": "Ren suốt tyren trong bản vẽ 9.3.03844 dùng loại gì, kích thước bao nhiêu?",
        "expected": "M8x52mm hoặc 1/4-20UNC",
    },

    # --- CẤP 4: BẪY (Ngoài phạm vi dữ liệu) ---
    {
        "id": 16,
        "level": "⚫ CẤP 4 - BẪY",
        "question": "Giá thép inox 304 hôm nay là bao nhiêu?",
        "expected": "TỪ CHỐI",
    },
    {
        "id": 17,
        "level": "⚫ CẤP 4 - BẪY",
        "question": "Nhà cung cấp inox cho công ty Quốc Trường là ai?",
        "expected": "TỪ CHỐI",
    },
    {
        "id": 18,
        "level": "⚫ CẤP 4 - BẪY",
        "question": "Nhiệt độ hàn laser phù hợp cho inox 201 là bao nhiêu?",
        "expected": "TỪ CHỐI",
    },
    {
        "id": 19,
        "level": "⚫ CẤP 4 - BẪY",
        "question": "Bản vẽ mã 9.3.05000 có ghi chú gì đặc biệt không?",
        "expected": "TỪ CHỐI",
    },
    {
        "id": 20,
        "level": "⚫ CẤP 4 - BẪY",
        "question": "Thời tiết ngày mai thế nào?",
        "expected": "TỪ CHỐI",
    },
]

# ==========================================
# CHẠY TEST
# ==========================================
REJECT_KEYWORDS = ["không tìm thấy", "không có trong", "không thể", "ngoài phạm vi", "không có thông tin"]

results = []
print("=" * 70)
print("🧪 BẮT ĐẦU CHẠY BỘ TEST CHATBOT RAG — 20 CÂU HỎI")
print("=" * 70)

for tc in test_cases:
    print(f"\n{'─' * 60}")
    print(f"📌 Câu {tc['id']} | {tc['level']}")
    print(f"❓ {tc['question']}")
    print(f"🎯 Đáp án kỳ vọng: {tc['expected']}")
    
    start = time.time()
    try:
        stream, ref_text, ref_images, new_part_ids, debug_info = chat_with_rag(tc["question"])
        # Bot trả về stream (generator), ta phải đọc hết để ráp thành câu trả lời hoàn chỉnh
        answer = "".join(list(stream))
        if ref_text:
            answer += "\n" + ref_text
    except Exception as e:
        answer = f"[LỖI] {e}"
    elapsed = time.time() - start
    
    print(f"🤖 Bot trả lời ({elapsed:.1f}s):\n{answer}")
    
    # Đánh giá kết quả
    answer_lower = answer.lower()
    if tc["expected"] == "TỪ CHỐI":
        passed = any(kw in answer_lower for kw in REJECT_KEYWORDS)
    else:
        # Kiểm tra từng keyword trong expected (phân cách bằng dấu phẩy hoặc "hoặc")
        keywords = [k.strip().lower() for k in tc["expected"].replace(" hoặc ", ",").split(",")]
        passed = any(kw in answer_lower for kw in keywords)
    
    status = "PASS" if passed else "FAIL"
    print(f"\n📊 Kết quả: {status}")
    results.append({"id": tc["id"], "level": tc["level"], "passed": passed, "time": elapsed})

# ==========================================
# TỔNG KẾT
# ==========================================
print("\n" + "=" * 70)
print("📊 BẢNG TỔNG KẾT KẾT QUẢ TEST")
print("=" * 70)

total_pass = sum(1 for r in results if r["passed"])
total = len(results)

# Theo cấp độ
for level_name in ["🟢 CẤP 1 - DỄ", "🟡 CẤP 2 - TRUNG BÌNH", "🔴 CẤP 3 - KHÓ", "⚫ CẤP 4 - BẪY"]:
    level_results = [r for r in results if r["level"] == level_name]
    level_pass = sum(1 for r in level_results if r["passed"])
    level_total = len(level_results)
    avg_time = sum(r["time"] for r in level_results) / max(len(level_results), 1)
    print(f"  {level_name}: {level_pass}/{level_total} ({avg_time:.1f}s avg)")

print(f"\n🏆 TỔNG ĐIỂM: {total_pass}/{total} "
      f"({'XUẤT SẮC ' if total_pass >= 18 else 'TỐT 👍' if total_pass >= 14 else 'CẦN CẢI THIỆN ' if total_pass >= 10 else 'YẾU '})")
print(f"⏱️ Tổng thời gian: {sum(r['time'] for r in results):.1f}s")
