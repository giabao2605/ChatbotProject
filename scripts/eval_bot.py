import sys
import os
import json

# Add parent dir to path to import rag_logic
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

from rag_logic import chat_with_rag

questions = [
    # Mức 1: Cơ bản
    "Cho tôi biết vật liệu và độ dày của mã chi tiết 9.1.00678-ver02 là gì?",
    "Cụm chi tiết 9.3.03843(975-122) cần phải đi qua những tổ sản xuất nào?",
    "Yêu cầu kỹ thuật chung (YCKT) khi gia công mã 9.3.03951(HCP7235-STK) là gì?",
    
    # Mức 2: Trung bình
    "Hãy tóm tắt quy trình gia công cho mã 9.1.00678-ver02 từ lúc nhận phôi đến khi hoàn thành.",
    "So sánh sự khác nhau về vật liệu và các bước gia công của hai mã 9.3.03843(975-122) và 9.3.03844(975-123).",
    "Dung sai kích thước và dung sai hình học của mã 9.1.00678-ver02 được quy định như thế nào?",
    
    # Mức 3: Nâng cao
    "Trong cụm tổ hàn của mã 9.3.03843(975-122), nó được hàn ghép lại từ những chi tiết con (mã BTP) nào? Số lượng mỗi chi tiết con là bao nhiêu?",
    "Trên bản vẽ gia công phay/tiện của mã 9.3.03843(975-122), có yêu cầu độ nhám bề mặt (Ra, Rz) không? Chỉ rõ vị trí cần làm nhám."
]

results = []

for i, q in enumerate(questions, 1):
    print(f"\n[{i}/{len(questions)}] Đang kiểm tra: {q}")
    try:
        stream, ref_text, ref_images, parts, debug_info = chat_with_rag(q)
        answer = ""
        for chunk in stream:
            answer += chunk
        
        results.append({
            "question": q,
            "answer": answer,
            "references": ref_text,
            "ref_images": len(ref_images)
        })
    except Exception as e:
        print(f"Error processing question {i}: {e}")
        results.append({
            "question": q,
            "answer": f"ERROR: {e}",
            "references": "",
            "ref_images": 0
        })

with open("scripts/eval_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=4)
print("Hoàn thành đánh giá. Kết quả đã được lưu vào scripts/eval_results.json")
