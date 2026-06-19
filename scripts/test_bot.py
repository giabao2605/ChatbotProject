import sys
import os
sys.stdout.reconfigure(encoding='utf-8')

from rag_logic import chat_with_rag

questions = [
    "Hãy liệt kê vật liệu chính của khung thành phẩm mã 9.3.03843 và vật liệu của các linh kiện phụ (như cụm hoa văn, chỉ hoa văn) thuộc mã này. Phân tích rõ ràng từng loại.",
    "Hãy so sánh chi tiết mã 9.3.03844 và 9.3.03843 về: Vật liệu chính, Vật liệu phụ, Kích thước tổng thể và Các yêu cầu kỹ thuật (YCKT) riêng biệt cho công đoạn sơn.",
    "Dung sai độ phẳng bề mặt của mã 9.3.03843 là bao nhiêu?",
    "Cụm hoa văn D40x74mm (thuộc mã 9.3.03843) sau khi gia công xong ở Tổ nhám trụ sẽ được luân chuyển tiếp sang tổ nào?"
]

for i, q in enumerate(questions, 1):
    print("\n" + "="*80)
    print(f"CÂU HỎI {i}: {q}")
    print("="*80)
    stream, ref_text, ref_images, parts, debug_info = chat_with_rag(q)
    print("\nTRẢ LỜI:")
    for chunk in stream:
        print(chunk, end='', flush=True)
    print(ref_text)
