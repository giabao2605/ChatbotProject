# -*- coding: utf-8 -*-
"""Auto-split tu ingestion/pdf_processor.py (P1.3). Giu nguyen logic goc; chi tach file + import."""

import os
import re
import json
from PIL import Image
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception
from mech_chatbot.config.logging import logger
from mech_chatbot.llm.vision_client import is_retryable_error

# cross-module (owned) imports
from mech_chatbot.ingestion.pdf.config import IMAGE_DIR


@retry(
    retry=retry_if_exception(is_retryable_error),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(5)
)
def call_vision_model(vision_model, prompt, image=None):
    if image:
        return vision_model.generate_content([prompt, image])
    else:
        return vision_model.generate_content(prompt)


def parse_vision_json(raw_text):
    """Parse JSON tu vision model tra ve, ho tro fallback regex."""
    try:
        json_str = raw_text
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0].strip()
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0].strip()
        else:
            match = re.search(r'\{.*\}', json_str, re.DOTALL)
            if match:
                json_str = match.group(0)
                
        data = json.loads(json_str)
        return data
    except Exception as e:
        logger.warning(f"Khong the parse JSON tu vision: {e}")
        return None


def format_vision_data(data):
    """Format dictionary thanh text de dua vao Qdrant"""
    if not data:
        return ""
    
    parts = []
    if data.get("document_codes"):
        parts.append(f"- Mã bản vẽ/tài liệu: {', '.join([str(x) for x in data['document_codes']])}")
    if data.get("part_names"):
        parts.append(f"- Tên chi tiết/vật tư: {', '.join([str(x) for x in data['part_names']])}")
    if data.get("materials"):
        parts.append(f"- Vật liệu: {', '.join([str(x) for x in data['materials']])}")
    if data.get("dimensions"):
        parts.append(f"- Kích thước nổi bật: {', '.join([str(x) for x in data['dimensions']])}")
    if data.get("tolerances"):
        parts.append(f"- Dung sai: {', '.join([str(x) for x in data['tolerances']])}")
    if data.get("technical_notes"):
        parts.append("- Ghi chú kỹ thuật:\n  " + "\n  ".join([str(x) for x in data["technical_notes"]]))
    if data.get("bom_rows"):
        parts.append("- Các hàng vật tư (BOM) nhận diện được:\n  " + "\n  ".join([str(row) for row in data["bom_rows"]]))
    if data.get("uncertain_fields"):
        parts.append(f"- Các phần mờ/không chắc chắn: {', '.join([str(x) for x in data['uncertain_fields']])}")
        
    return "Kết quả phân tích ảnh (Cấu trúc JSON):\n" + "\n".join(parts) if parts else ""


def _prewarm_vision_cache(doc, ten_file, thu_muc, domain, vision_model, progress_callback=None):
    """Perf (GD3, OPT-IN): lam nong Vision cache SONG SONG de tang toc ingest.

    MAC DINH TAT: chi chay khi env INGEST_VISION_PREWARM_WORKERS > 1.
    An toan: CHI ghi vao Vision cache (disk) theo hash anh; vong lap chinh KHONG doi.
    Khi TAT -> no-op tuyet doi (hanh vi y het ban serial cu).
    Render anh chay TUAN TU (PyMuPDF khong thread-safe); chi goi Vision API song song (I/O).

    LUU Y QUAN TRONG: prompt + dieu kien `vision_required` o day PHAI KHOP voi vong lap
    chinh ben duoi. Neu sua prompt/dieu kien trong vong lap, PHAI sua o ca day.
    Bat buoc chay golden-file consistency test truoc khi bat that o production.
    """
    try:
        max_workers = int(os.getenv("INGEST_VISION_PREWARM_WORKERS", "1"))
    except ValueError:
        max_workers = 1
    if max_workers <= 1 or vision_model is None or doc is None:
        return  # TAT -> khong lam gi (giu nguyen hanh vi serial)
    try:
        from mech_chatbot.ingestion.domain_handlers import get_handler as _gh
        _mech = _gh(domain).vision_always
        from mech_chatbot.ingestion import vision_cache as _vc
        base_name = os.path.splitext(ten_file)[0]
        safe_thu_muc = re.sub(r'[\\/*?:"<>|]', "", thu_muc) if thu_muc else ""
        dpi = int(os.getenv("PDF_RENDER_DPI", "300"))
        tasks = []  # (img_path, prompt, key)
        for page_num in range(len(doc)):
            try:
                page = doc.load_page(page_num)
                text = page.get_text("text")
                is_text_heavy = len(text.strip()) > 1500
                if not (_mech or not is_text_heavy):
                    continue  # trang nay vong lap se KHONG goi Vision -> bo qua, khoi ton quota
                pix = page.get_pixmap(dpi=dpi)
                img_name = (f"{safe_thu_muc}_{base_name}_page{page_num+1}.png"
                            if safe_thu_muc else f"{base_name}_page{page_num+1}.png")
                img_path = os.path.join(IMAGE_DIR, img_name)
                pix.save(img_path)
                pix = None
                key = _vc.hash_image_file(img_path)
                if key is None or _vc.get(key) is not None:
                    continue  # da co cache -> bo qua
                prompt = (
                    f"Day la trang so {page_num+1} cua file {ten_file}. "
                    "Hay OCR va tra ve ket qua DUOI DANG JSON voi schema sau:\n"
                    "{\n"
                    '  "document_codes": [],\n'
                    '  "part_names": [],\n'
                    '  "materials": [],\n'
                    '  "dimensions": [],\n'
                    '  "tolerances": [],\n'
                    '  "technical_notes": [],\n'
                    '  "bom_rows": [],\n'
                    '  "uncertain_fields": []\n'
                    "}\n"
                    "Luon tra ve dung dinh dang JSON (khong kem text mo dau/ket thuc ngoai block ```json). "
                    "Dien vao cac mang cac thong tin ky thuat tuong ung ban nhin thay trong hinh."
                )
                tasks.append((img_path, prompt, key))
            except Exception as _e:
                logger.warning(f"[prewarm] render trang {page_num+1} loi: {_e}")
        if not tasks:
            return
        if progress_callback:
            progress_callback(f"Pre-warm Vision song song {len(tasks)} trang (workers={max_workers})...")
        from concurrent.futures import ThreadPoolExecutor

        def _one(t):
            img_path, prompt, key = t
            try:
                img = Image.open(img_path)
                resp = call_vision_model(vision_model, prompt, img)
                vd = parse_vision_json(resp.text)
                if vd:
                    _vc.put(key, vd)
            except Exception as _e:
                logger.warning(f"[prewarm] Vision loi ({img_path}): {_e}")

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            list(ex.map(_one, tasks))
    except Exception as _e:
        logger.warning(f"[prewarm] bo qua do loi: {_e}")

__all__ = [
    'call_vision_model',
    'parse_vision_json',
    'format_vision_data',
    '_prewarm_vision_cache',
]
