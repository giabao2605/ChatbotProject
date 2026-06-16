import os
import fitz
import unicodedata
import re
import time
import json
import html
from PIL import Image
import pdfplumber
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception
from transformers import AutoTokenizer
import underthesea
from qdrant_client import models
from logger_config import logger
# FIX #1: dung 2 ham moi (reset 1 lan + insert tung trang). Giu save_document_metadata cho file 1 trang.
from db_logic import reset_document_metadata, save_page_metadata, save_document_metadata
from rag_logic import vectorstore, client
# FIX Bug #4: predicate retry dung chung cho Gemini (google-genai)
from gemini_client import is_retryable_error
from functools import lru_cache

def remove_accents(text: str) -> str:
    if text is None:
        return ""
    text = str(text)
    text = text.replace("đ", "d").replace("Đ", "D")
    normalized = unicodedata.normalize("NFD", text)
    return "".join(
        ch for ch in normalized
        if unicodedata.category(ch) != "Mn"
    )
 
# FIX H5: Cache word_tokenize (underthesea cham 50-200ms/call). Header / chunk lap lai khong tokenize lai.
@lru_cache(maxsize=4096)
def tokenize_cached(text):
    return underthesea.word_tokenize(text, format="text")
 
# Khoi tao Tokenizer Singleton de do luong chunk size chinh xac
try:
    GLOBAL_TOKENIZER = AutoTokenizer.from_pretrained("keepitreal/vietnamese-sbert")
    logger.info("Da load AutoTokenizer (keepitreal/vietnamese-sbert) thanh cong cho Chunking.")
except Exception as e:
    logger.warning(f"Khong the load AutoTokenizer: {e}. Fallback ve dem ky tu.")
    GLOBAL_TOKENIZER = None
 
def tokenizer_length(text):
    if GLOBAL_TOKENIZER:
        return len(GLOBAL_TOKENIZER.encode(text))
    return len(text)
 
# Token-based Text Splitter dung chung cho toan bo module
token_splitter = RecursiveCharacterTextSplitter(
    chunk_size=480,
    chunk_overlap=80,
    length_function=tokenizer_length
)
 
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_DIR = os.path.join(BASE_DIR, "Data_Anh_Da_Tach")
os.makedirs(IMAGE_DIR, exist_ok=True)
 
try:
    import pandas as pd
except ImportError:
    pd = None
 
try:
    import docx
except ImportError:
    docx = None
 
try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None
 
try:
    from pptx import Presentation
except ImportError:
    Presentation = None
 
PDF_EXTENSIONS = {".pdf"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".sql",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".cs",
    ".cpp", ".c", ".h", ".hpp", ".json", ".xml", ".yaml",
    ".yml", ".ini", ".cfg",
}
HTML_EXTENSIONS = {".html", ".htm"}
TABLE_EXTENSIONS = {".csv", ".tsv", ".xlsx", ".xls"}
WORD_EXTENSIONS = {".docx"}
PRESENTATION_EXTENSIONS = {".pptx"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff"}
 
SUPPORTED_LEARNING_EXTENSIONS = (
    PDF_EXTENSIONS
    | TEXT_EXTENSIONS
    | HTML_EXTENSIONS
    | TABLE_EXTENSIONS
    | WORD_EXTENSIONS
    | PRESENTATION_EXTENSIONS
    | IMAGE_EXTENSIONS
)
 
# 1. Ham trich xuat thong minh (Di chuyen tu file cu sang)
def extract_metadata_smart(text, ten_file, thu_muc, vision_model=None):
    """
    Chien luoc: Regex-first -> Gemini-fallback.
    Luu y: LLM chi duoc goi khi Regex tra ve "Khong ro".
    Regex sai (false positive) se khong duoc LLM tu dong sua.
    Neu format ban ve thay doi dot ngot, kiem tra Regex truoc.
    """
    lines = text.split('\n')
 
    ma_doi_tuong_regex = []
    code_patterns = [
        r"\b\d{1,2}\.\d{1,2}\.\d{3,6}(?:\.\d{1,4})?\b",
        r"\b[A-Z]{1,5}[-_/]?\d{3,8}(?:[-_/][A-Z0-9]+)?\b",
    ]
    
    for pat in code_patterns:
        for m in re.findall(pat, ten_file):
            if m not in ma_doi_tuong_regex:
                ma_doi_tuong_regex.append(m)

    ten_sp_val = "Khong ro"
    vat_lieu_val = "Khong ro"
    
    for idx, line in enumerate(lines):
        line_stripped = line.strip()
        
        for pat in code_patterns:
            for m in re.findall(pat, line_stripped):
                if m not in ma_doi_tuong_regex:
                    ma_doi_tuong_regex.append(m)
                    if ten_sp_val == "Khong ro" and idx + 1 < len(lines):
                        ten_sp_parts = []
                        for j in range(idx + 1, min(idx + 3, len(lines))):
                            next_line = lines[j].strip()
                            if next_line.startswith("Ban ve") or next_line == "" or next_line == "-":
                                break
                            ten_sp_parts.append(next_line)
                        if ten_sp_parts:
                            ten_sp_val = " ".join(ten_sp_parts)
                            
        if re.match(r'^(?:Inox|SUS|SS|AL|Thep|SPCC|Q235|Nhom|Dong|Sat)', line_stripped, re.IGNORECASE):
            if vat_lieu_val == "Khong ro":
                vat_lieu_val = line_stripped

    so_luong_val = "Khong ro"
    found_vat_lieu = False
    for idx, line in enumerate(lines):
        line_stripped = line.strip()
        if re.match(r'^(?:Inox|SUS|SS|AL|Thep|SPCC|Q235|Nhom|Dong|Sat)', line_stripped, re.IGNORECASE):
            found_vat_lieu = True
            continue
        if found_vat_lieu and re.match(r'^\d{1,3}$', line_stripped):
            so_luong_val = line_stripped
            break
        if found_vat_lieu and line_stripped:
            break
 
    cong_doan_val = "Khong ro"
    cong_doan_match = re.search(r'Ban ve\s+(To\s+[\w\s]+?)(?:\n|$)', text)
    if cong_doan_match:
        cong_doan_val = cong_doan_match.group(1).strip()
    else:
        folder_map = {
            "To_Han": "To han", "To_Nham": "To nham", "To_Son": "To son",
            "To_Dong_Goi": "To dong goi", "To_Tien_Phay": "To Tien Phay",
        }
        cong_doan_val = folder_map.get(thu_muc, thu_muc)
 
    ngay_ve_val = "Khong ro"
    ngay_ve_match = re.search(r'(\d{2}/\d{2}/\d{4})', text)
    if ngay_ve_match:
        ngay_ve_val = ngay_ve_match.group(1)
 
    nguoi_lap_val = "Khong ro"
    for idx, line in enumerate(lines):
        if "Ten san pham" in line and ":" in line:
            if idx + 1 < len(lines):
                next_line = lines[idx + 1].strip()
                if next_line and next_line != "Ma BTP :" and not next_line.startswith("CONG TY"):
                    nguoi_lap_val = next_line
            break
 
    dung_sai_day = "Khong ro"
    dung_sai_khac = "Khong ro"
    ds_day_match = re.search(r'Dung sai do day vat lieu\s*:\s*([^\n]+)', text)
    if ds_day_match:
        dung_sai_day = ds_day_match.group(1).strip()
    ds_khac_match = re.search(r'Dung sai cac kich thuoc khac\s*:\s*([^\n]+)', text)
    if ds_khac_match:
        dung_sai_khac = ds_khac_match.group(1).strip()
 
    yckt_text = ""
    in_yckt = False
    for line in lines:
        line_stripped = line.strip()
        if line_stripped.startswith("Ban ve To"):
            in_yckt = True
            continue
        if in_yckt:
            if re.match(r'^9\.3\.\d{4,5}$', line_stripped):
                break
            if line_stripped.startswith("-") and len(line_stripped) > 1:
                yckt_text += line_stripped[1:].strip() + "\n"
            elif line_stripped and line_stripped != "-":
                if yckt_text and not yckt_text.endswith("\n"):
                    yckt_text += " " + line_stripped
                elif yckt_text:
                    yckt_text = yckt_text.rstrip("\n") + " " + line_stripped + "\n"
 
    hdcv_val = ""
    all_hdcv = re.findall(r'HDCV:\s*([^\n]+)', text)
    if len(all_hdcv) > 0:
        hdcv_val = " | ".join(all_hdcv)
 
    kich_thuoc_val = ""
    kt_match = re.search(r'Kich thuoc tong the\s*:\s*([^\n]+)', text)
    if kt_match:
        kich_thuoc_val = kt_match.group(1).strip()
    elif re.search(r'(\d{2,4}\s*[xX]\s*\d{2,4}\s*[xX]\s*\d{2,4})\s*mm', text):
        kich_thuoc_val = re.search(r'(\d{2,4}\s*[xX]\s*\d{2,4}\s*[xX]\s*\d{2,4})\s*mm', text).group(1) + "mm"
 
    result = {
        "ma_doi_tuong": ma_doi_tuong_regex,
        "ten_tai_lieu": ten_sp_val,
        "loai_tai_lieu": "Ban ve gia cong",  # Default
        "cong_doan": cong_doan_val, "vat_lieu": vat_lieu_val, "so_luong": so_luong_val,
        "nguoi_lap": nguoi_lap_val, "ngay_ve": ngay_ve_val, "dung_sai_day": dung_sai_day,
        "dung_sai_khac": dung_sai_khac, "kich_thuoc": kich_thuoc_val,
        "yckt": yckt_text.strip(), "hdcv": hdcv_val
    }
 
    # HYBRID APPROACH: LLM Extraction de doc moi ma (V2)
    # KHONG BO QUA GOI GEMINI: Cho phep Gemini bo sung hoac sua ma tu Regex
    if vision_model:
        prompt = f"""
        Ban la chuyen gia doc tai lieu co khi. Hay trich xuat cac thong tin sau tu doan text, tra ve dung dinh dang JSON:
            "ma_doi_tuong": ["ma 1", "ma 2"],
            "ten_tai_lieu": "Ten san pham hoac tieu de tai lieu",
            "loai_tai_lieu": "Nhan ngan gon mo ta tai lieu (VD: Ban ve gia cong, So tay ISO, Catalog...)",
            "vat_lieu": "Vat lieu de cap (neu co)"
            
        Goi y cac thong tin so bo da tim thay (hay kiem tra, mo rong hoac sua lai neu can):
        - Ma doi tuong: {result.get("ma_doi_tuong", [])}
        - Ten tai lieu: {result.get("ten_tai_lieu")}
        - Vat lieu: {result.get("vat_lieu")}
        
        Uu tien ket qua phan tich cua ban neu hop ly hon. Luu y: Chi tra ve dung JSON, khong giai thich gi them.
        Text can phan tich:
        {text}
        """
        try:
            import json
            response = call_gemini_vision(vision_model, prompt)
            raw_json = response.text.replace('```json', '').replace('```', '').strip()
            llm_result = json.loads(raw_json)
            if "ma_doi_tuong" in llm_result and isinstance(llm_result["ma_doi_tuong"], list) and llm_result["ma_doi_tuong"]:
                result["ma_doi_tuong"] = [str(x) for x in llm_result["ma_doi_tuong"]]
            if "ten_tai_lieu" in llm_result and llm_result["ten_tai_lieu"] and llm_result["ten_tai_lieu"] != "Khong ro":
                result["ten_tai_lieu"] = str(llm_result["ten_tai_lieu"]).strip()
            if "loai_tai_lieu" in llm_result and llm_result["loai_tai_lieu"]:
                result["loai_tai_lieu"] = str(llm_result["loai_tai_lieu"]).strip()
            if "vat_lieu" in llm_result and llm_result["vat_lieu"] and llm_result["vat_lieu"] != "Khong ro":
                result["vat_lieu"] = str(llm_result["vat_lieu"]).strip()
        except Exception as e:
            logger.error(f"Loi LLM Fallback boc tach metadata cho {ten_file}: {e}")
 
    return result
 
# 2. Co che Retry thong minh cho Gemini chong 429 (da migrate sang google-genai)
@retry(
    retry=retry_if_exception(is_retryable_error),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    stop=stop_after_attempt(5)
)
def call_gemini_vision(vision_model, prompt, image=None):
    if image:
        return vision_model.generate_content([prompt, image])
    else:
        return vision_model.generate_content(prompt)
 
def _require_package(package, feature_name):
    if package is None:
        raise ImportError(f"Thieu thu vien de doc {feature_name}. Hay cai/cap nhat requirements.txt roi chay lai.")
 
def _read_text_file(file_path):
    encodings = ("utf-8-sig", "utf-8", "cp1258", "cp1252", "latin-1")
    last_error = None
    for encoding in encodings:
        try:
            with open(file_path, "r", encoding=encoding) as f:
                return f.read()
        except UnicodeDecodeError as exc:
            last_error = exc
    raise UnicodeDecodeError(
        last_error.encoding,
        last_error.object,
        last_error.start,
        last_error.end,
        "Khong doc duoc file bang cac encoding pho bien.",
    )
 
def _read_json_file(file_path):
    raw_text = _read_text_file(file_path)
    try:
        data = json.loads(raw_text)
        return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception:
        return raw_text
 
def _read_xml_file(file_path):
    raw_text = _read_text_file(file_path)
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(raw_text)
        extracted = "\n".join(t.strip() for t in root.itertext() if t and t.strip())
        return extracted or raw_text
    except Exception:
        return raw_text
 
def _read_html_file(file_path):
    raw_text = _read_text_file(file_path)
    if BeautifulSoup:
        soup = BeautifulSoup(raw_text, "html.parser")
        return soup.get_text("\n")
    without_tags = re.sub(r"<[^>]+>", " ", raw_text)
    return html.unescape(without_tags)
 
def _dataframe_to_text(df, title=None):
    if df is None or df.empty:
        return f"{title}\n(Bang rong)" if title else "(Bang rong)"
    df = df.fillna("")
    text = df.to_string(index=False)
    return f"{title}\n{text}" if title else text
 
def _read_table_file(file_path, ext):
    _require_package(pd, "bang tinh/CSV")
    if ext in {".csv", ".tsv"}:
        sep = "\t" if ext == ".tsv" else ","
        last_error = None
        for encoding in ("utf-8-sig", "utf-8", "cp1258", "cp1252", "latin-1"):
            try:
                df = pd.read_csv(file_path, sep=sep, encoding=encoding)
                return _dataframe_to_text(df, title=f"Bang du lieu tu {os.path.basename(file_path)}")
            except UnicodeDecodeError as exc:
                last_error = exc
        raise last_error
    sheets = pd.read_excel(file_path, sheet_name=None)
    sheet_texts = []
    for sheet_name, df in sheets.items():
        sheet_texts.append(_dataframe_to_text(df, title=f"Sheet: {sheet_name}"))
    return "\n\n---\n\n".join(sheet_texts)
 
def _read_word_file(file_path):
    _require_package(docx, "Word DOCX")
    document = docx.Document(file_path)
    parts = [para.text.strip() for para in document.paragraphs if para.text and para.text.strip()]
    for table_index, table in enumerate(document.tables, start=1):
        rows = []
        for row in table.rows:
            cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
            rows.append(" | ".join(cells))
        if rows:
            parts.append(f"Bang {table_index}:\n" + "\n".join(rows))
    return "\n\n".join(parts)
 
def _read_presentation_file(file_path):
    _require_package(Presentation, "PowerPoint PPTX")
    prs = Presentation(file_path)
    slides = []
    for slide_index, slide in enumerate(prs.slides, start=1):
        parts = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = shape.text.strip()
                if text:
                    parts.append(text)
            if getattr(shape, "has_table", False):
                rows = []
                for row in shape.table.rows:
                    cells = [cell.text.replace("\n", " ").strip() for cell in row.cells]
                    rows.append(" | ".join(cells))
                if rows:
                    parts.append("Bang:\n" + "\n".join(rows))
        if parts:
            slides.append(f"Slide {slide_index}:\n" + "\n\n".join(parts))
    return "\n\n---\n\n".join(slides)
 
def _read_image_file(file_path, ten_file, vision_model):
    if not vision_model:
        raise ValueError("File anh can GOOGLE_API_KEY hop le de Gemini Vision doc noi dung/OCR.")
    image = Image.open(file_path)
    prompt = (
        f"Day la file anh '{ten_file}' duoc nap lam du lieu cho chatbot ky thuat. "
        "Hay OCR va mo ta day du moi chu, bang, ma so, kich thuoc, thong so, ghi chu ky thuat, "
        "vat lieu, quy trinh hoac chi tiet co khi nhin thay trong anh. "
        "Tra loi bang tieng Viet, trinh bay co cau truc de dung lam du lieu RAG."
    )
    response = call_gemini_vision(vision_model, prompt, image)
    return response.text
 
def extract_text_from_supported_file(file_path, ten_file, vision_model=None):
    ext = os.path.splitext(file_path)[1].lower()
    if ext in {".json"}:
        return _read_json_file(file_path), "du_lieu_json"
    if ext in {".xml"}:
        return _read_xml_file(file_path), "du_lieu_xml"
    if ext in HTML_EXTENSIONS:
        return _read_html_file(file_path), "van_ban_html"
    if ext in TABLE_EXTENSIONS:
        return _read_table_file(file_path, ext), "bang_du_lieu"
    if ext in WORD_EXTENSIONS:
        return _read_word_file(file_path), "van_ban_word"
    if ext in PRESENTATION_EXTENSIONS:
        return _read_presentation_file(file_path), "slide"
    if ext in IMAGE_EXTENSIONS:
        return _read_image_file(file_path, ten_file, vision_model), "image_summary"
    if ext in TEXT_EXTENSIONS:
        return _read_text_file(file_path), "van_ban"
    supported = ", ".join(sorted(SUPPORTED_LEARNING_EXTENSIONS))
    raise ValueError(f"Dinh dang {ext or '(khong co duoi file)'} chua duoc ho tro. Cac dinh dang dang ho tro: {supported}")
 
# 3. Ham xu ly PDF trung tam
def process_and_ingest_pdf(pdf_path, ten_file, thu_muc, vision_model=None, progress_callback=None):
    start_time = time.time()
    report = {
        "status": "success",
        "ten_file": ten_file,
        "total_pages": 0,
        "total_chunks": 0,
        "failed_pages": [],
        "time_taken": 0,
        "message": ""
    }
    doc = None
    pdf_table_reader = None
    try:
        doc = fitz.open(pdf_path)
        report["total_pages"] = len(doc)
 
        # FIX #1: Reset metadata MOT LAN cho ca file, lay doc_id dung chung cho moi trang
        doc_id = reset_document_metadata(ten_file, thu_muc)
 
        # FIX hieu nang: mo pdfplumber MOT LAN ngoai vong lap (truoc day mo lai moi trang)
        try:
            pdf_table_reader = pdfplumber.open(pdf_path)
        except Exception as e:
            logger.warning(f"Khong mo duoc pdfplumber cho {ten_file}: {e}")
            pdf_table_reader = None
 
        base_name = os.path.splitext(ten_file)[0]  # FIX: thay ten_file.replace('.pdf', '')
 
        for page_num in range(len(doc)):
            if progress_callback:
                progress_callback(f"Dang xu ly trang {page_num+1}/{len(doc)}")
            try:
                page = doc.load_page(page_num)
                text = page.get_text("text")

                # Render image truoc de kip phan tich
                pix = page.get_pixmap(dpi=200)
                safe_thu_muc = re.sub(r'[\\/*?:"<>|]', "", thu_muc) if thu_muc else ""
                if safe_thu_muc:
                    img_name = f"{safe_thu_muc}_{base_name}_page{page_num+1}.png"
                else:
                    img_name = f"{base_name}_page{page_num+1}.png"
                img_path = os.path.join(IMAGE_DIR, img_name)
                pix.save(img_path)

                image_summary = ""
                is_pure_drawing = len(text.strip()) < 200  # Tang nguong tu 50 len 200 (Fix Bug #10)
                
                # So bo lay ID bang regex tu text goc de xem co ma khong (tiet kiem API)
                temp_info = extract_metadata_smart(text, ten_file, thu_muc, None)
                has_valid_id = len(temp_info.get("ma_doi_tuong", [])) > 0

                # CHI GOI GEMINI VISION KHI: Trang khong co ma HOAC la ban ve thuan (it text)
                if vision_model and os.path.exists(img_path) and (not has_valid_id or is_pure_drawing):
                    if progress_callback:
                        progress_callback(f"Dang dung AI (Gemini) phan tich anh trang {page_num+1}...")
                    try:
                        img_to_analyze = Image.open(img_path)
                        prompt = (
                            f"Day la trang so {page_num+1} cua file {ten_file}. "
                            f"Hay mo ta chi tiet nhung gi ban thay trong hinh anh nay: "
                            f"hinh dang linh kien, cac goc nhin mat cat, ghi chu ky thuat, cac thong so/kich thuoc quan trong, "
                            f"hoac so do huong dan cong viec neu co. "
                            f"Mo ta cua ban se duoc dung de tra cuu RAG, vi vay hay trich xuat bat ky thong tin nao huu ich. Tra loi bang tieng Viet."
                        )
                        response = call_gemini_vision(vision_model, prompt, img_to_analyze)
                        image_summary = response.text
                    except Exception as e:
                        logger.error(f"Loi khi dung Gemini phan tich {img_name}: {e}")

                combined_text_for_metadata = text + "\n\n" + image_summary
                info = extract_metadata_smart(combined_text_for_metadata, ten_file, thu_muc, vision_model)

                metadata = {
                    "file_goc": ten_file,
                    "phong_ban_quyen": thu_muc,
                    "ma_doi_tuong": info["ma_doi_tuong"],
                    "loai_tai_lieu": info["loai_tai_lieu"],
                    "ten_san_pham": info["ten_tai_lieu"],
                    "cong_doan": info["cong_doan"],
                    "so_luong": info["so_luong"],
                    "vat_lieu": info["vat_lieu"],
                    "nguoi_lap": info["nguoi_lap"],
                    "ngay_ve": info["ngay_ve"],
                    "dung_sai_do_day": info["dung_sai_day"],
                    "dung_sai_kich_thuoc": info["dung_sai_khac"],
                    "kich_thuoc_tong_the": info["kich_thuoc"],
                    "trang_so": page_num + 1,
                }
                info['trang_so'] = page_num + 1
 
                # FIX #1: CHI insert metadata trang nay (khong xoa metadata cac trang khac)
                save_page_metadata(ten_file, thu_muc, info, doc_id=doc_id)
 
                all_chunks = []
                title_block = (
                    f"Thong tin tai lieu {ten_file}:\n"
                    f"- Ma doi tuong: {info['ma_doi_tuong']}\n"
                    f"- Loai tai lieu: {info['loai_tai_lieu']}\n"
                    f"- Ten tai lieu/san pham: {info['ten_tai_lieu']}\n"
                    f"- Cong doan: {info['cong_doan']}\n"
                    f"- Vat lieu: {info['vat_lieu']}\n"
                    f"- So luong: {info['so_luong']}\n"
                    f"- Nguoi lap: {info['nguoi_lap']}\n"
                    f"- Ngay phat hanh: {info['ngay_ve']}\n"
                    f"- Dung sai do day vat lieu: {info['dung_sai_day']}\n"
                    f"- Dung sai cac kich thuoc khac: {info['dung_sai_khac']}\n"
                )
                if info['kich_thuoc']:
                    title_block += f"- Kich thuoc tong the: {info['kich_thuoc']}\n"
                all_chunks.append(Document(page_content=title_block, metadata={**metadata, "loai_du_lieu": "title_block"}))
 
                # FIX hieu nang: dung pdf_table_reader DA mo san thay vi mo lai moi trang
                markdown_tables = ""
                if pdf_table_reader is not None:
                    try:
                        page_plumber = pdf_table_reader.pages[page_num]
                        tables = page_plumber.extract_tables()
                        for table in tables:
                            for row in table:
                                cleaned_row = [str(cell).replace("\n", " ").strip() if cell else "" for cell in row]
                                markdown_tables += "| " + " | ".join(cleaned_row) + " |\n"
                            markdown_tables += "\n"
                    except Exception as e:
                        logger.warning(f"Khong the boc bang bieu {img_name}: {e}")
 
                if markdown_tables.strip():
                    table_content = f"Bang bieu tai lieu {ten_file} (Ma: {info['ma_doi_tuong']}):\n{markdown_tables}"
                    table_chunks = token_splitter.split_text(table_content)
                    for i, c in enumerate(table_chunks):
                        all_chunks.append(Document(page_content=c, metadata={**metadata, "loai_du_lieu": "bang_ke_vat_tu", "chunk_index": i}))
 
                if info['yckt']:
                    yckt_content = f"Yeu cau ky thuat tai lieu {ten_file} (Ma: {info['ma_doi_tuong']}):\n{info['yckt']}"
                    yckt_chunks = token_splitter.split_text(yckt_content)
                    for i, c in enumerate(yckt_chunks):
                        all_chunks.append(Document(page_content=c, metadata={**metadata, "loai_du_lieu": "yckt", "chunk_index": i}))
 
                if info['hdcv']:
                    hdcv_content = f"Huong dan cong viec tai lieu {ten_file} (Ma: {info['ma_doi_tuong']}):\n{info['hdcv']}"
                    hdcv_chunks = token_splitter.split_text(hdcv_content)
                    for i, c in enumerate(hdcv_chunks):
                        all_chunks.append(Document(page_content=c, metadata={**metadata, "loai_du_lieu": "hdcv", "chunk_index": i}))
 
                # Luoi an toan: luu raw text de giu moi thong tin chi tiet
                # (kich thuoc, goc, ban kinh, ghi chu) ma regex khong cover duoc
                if text.strip():
                    raw_content = f"Noi dung chi tiet trang {page_num+1} tai lieu {ten_file} (Ma: {info['ma_doi_tuong']}):\n{text.strip()}"
                    raw_chunks = token_splitter.split_text(raw_content)
                    for i, c in enumerate(raw_chunks):
                        all_chunks.append(Document(page_content=c, metadata={**metadata, "loai_du_lieu": "text", "chunk_index": i}))
 
                if image_summary.strip():
                    img_summary_content = f"Phan tich hinh anh tai lieu {ten_file} (Ma: {info['ma_doi_tuong']}):\n{image_summary}"
                    all_chunks.append(Document(page_content=img_summary_content, metadata={**metadata, "loai_du_lieu": "image_summary"}))
 
                # FIX #3: GIU text goc cho LLM (noi_dung_goc) TRUOC khi tokenize ban dung cho BM25
                for chunk in all_chunks:
                    chunk.metadata["noi_dung_goc"] = chunk.page_content
                    chunk.page_content = tokenize_cached(chunk.page_content)
 
                # Document Versioning: Xoa vector cu cua file nay truoc khi add (chi xoa 1 lan o trang 1)
                if page_num == 0:
                    try:
                        client.delete(
                            collection_name="TaiLieuKyThuat_v2",
                            points_selector=models.Filter(must=[
                                models.FieldCondition(key="metadata.file_goc", match=models.MatchValue(value=ten_file)),
                                models.FieldCondition(key="metadata.phong_ban_quyen", match=models.MatchValue(value=thu_muc))
                            ])
                        )
                    except Exception as e:
                        logger.error(f"Khong xoa duoc vector cu cua {ten_file}: {e}", exc_info=True)
                        raise
 
                vectorstore.add_documents(all_chunks)
                report["total_chunks"] += len(all_chunks)
 
            except Exception as e:
                logger.error(f"Loi khi xu ly trang {page_num+1} cua {ten_file}: {e}", exc_info=True)
                report["failed_pages"].append(page_num+1)
                # FIX #2: KHONG dong doc o day nua (truoc day doc.close() trong except
                # khien cac trang sau cua cung file deu loi day chuyen).
 
    except Exception as e:
        logger.error(f"Loi doc file PDF {ten_file}: {e}", exc_info=True)
        report["status"] = "error"
        report["message"] = str(e)
    finally:
        # FIX #2: dong tai nguyen DUNG MOT LAN, ke ca khi thanh cong
        # (truoc day thieu doc.close() o nhanh thanh cong -> ro ri file handle).
        if pdf_table_reader is not None:
            try:
                pdf_table_reader.close()
            except Exception:
                pass
        if doc is not None:
            doc.close()
 
    report["time_taken"] = round(time.time() - start_time, 2)
    if report["failed_pages"]:
        prefix = (report["message"] + " ") if report["message"] else ""
        report["message"] = prefix + f"(Cac trang loi: {report['failed_pages']})"
    if not report["message"]:
        report["message"] = f"Da nap {report['total_chunks']} chunks tu {report['total_pages']} trang."
    return report
 
def process_and_ingest_file(file_path, ten_file, thu_muc, vision_model=None, progress_callback=None):
    ext = os.path.splitext(file_path)[1].lower()
    start_time = time.time()
    report = {
        "status": "success",
        "ten_file": ten_file,
        "total_pages": 1,
        "total_chunks": 0,
        "failed_pages": [],
        "time_taken": 0,
        "message": ""
    }
    try:
        if progress_callback:
            progress_callback(f"Dang doc noi dung file {ext}...")
        text_content, data_type = extract_text_from_supported_file(file_path, ten_file, vision_model)
        text_content = text_content.strip()
        if not text_content:
            raise ValueError("Khong trich xuat duoc noi dung co the tim kiem tu file nay.")
 
        info = extract_metadata_smart(text_content[:5000], ten_file, thu_muc, vision_model)
        if info.get("ten_tai_lieu") == "Khong ro":
            info["ten_tai_lieu"] = os.path.splitext(ten_file)[0]
 
        # Neu file tu hoc la hinh anh, copy no sang Data_Anh_Da_Tach de sau nay lam Ban ve can cu
        if ext in IMAGE_EXTENSIONS:
            import shutil
            safe_thu_muc = re.sub(r'[\\/*?:"<>|]', "", thu_muc) if thu_muc else ""
            base_name = os.path.splitext(ten_file)[0]
            if safe_thu_muc:
                img_name = f"{safe_thu_muc}_{base_name}_page1.png"
            else:
                img_name = f"{base_name}_page1.png"
            img_path = os.path.join(IMAGE_DIR, img_name)
            # Convert sang PNG va luu
            try:
                img = Image.open(file_path)
                img.save(img_path, format="PNG")
            except Exception as e:
                logger.warning(f"Loi khi copy anh tu hoc vao Data_Anh_Da_Tach: {e}")
 
        if ext != ".pdf" and info.get("loai_tai_lieu") == "Ban ve gia cong":
            type_map = {
                "bang_du_lieu": "Bang du lieu",
                "van_ban_word": "Tai lieu Word",
                "slide": "Tai lieu trinh chieu",
                "image_summary": "Tai lieu anh/OCR",
                "van_ban_html": "Tai lieu HTML",
                "du_lieu_json": "Du lieu JSON",
                "du_lieu_xml": "Du lieu XML",
                "van_ban": "Tai lieu van ban",
            }
            info["loai_tai_lieu"] = type_map.get(data_type, "Tai lieu tong hop")
 
        metadata = {
            "file_goc": ten_file,
            "phong_ban_quyen": thu_muc,
            "ma_doi_tuong": info["ma_doi_tuong"],
            "loai_tai_lieu": info["loai_tai_lieu"],
            "ten_san_pham": info["ten_tai_lieu"],
            "cong_doan": info["cong_doan"],
            "so_luong": info["so_luong"],
            "vat_lieu": info["vat_lieu"],
            "nguoi_lap": info["nguoi_lap"],
            "ngay_ve": info["ngay_ve"],
            "dung_sai_do_day": info["dung_sai_day"],
            "dung_sai_kich_thuoc": info["dung_sai_khac"],
            "kich_thuoc_tong_the": info["kich_thuoc"],
            "trang_so": 1,
            "dinh_dang_file": ext,
        }
        info["trang_so"] = 1
 
        # File 1 trang: dung wrapper save_document_metadata (reset + insert 1 lan)
        save_document_metadata(ten_file, thu_muc, info)
 
        all_chunks = []
        title_block = (
            f"Thong tin tai lieu {ten_file}:\n"
            f"- Ma doi tuong: {info['ma_doi_tuong']}\n"
            f"- Loai tai lieu: {info['loai_tai_lieu']}\n"
            f"- Ten tai lieu/san pham: {info['ten_tai_lieu']}\n"
            f"- Cong doan/thu muc: {info['cong_doan']}\n"
            f"- Vat lieu: {info['vat_lieu']}\n"
            f"- Dinh dang file: {ext}\n"
        )
        all_chunks.append(Document(page_content=title_block, metadata={**metadata, "loai_du_lieu": "title_block"}))
 
        # Dung chung token_splitter da dinh nghia (480 tokens, 80 overlap)
        chunks = token_splitter.split_text(text_content)
        for i, chunk in enumerate(chunks):
            if chunk.strip():
                all_chunks.append(Document(
                    page_content=chunk,
                    metadata={**metadata, "loai_du_lieu": data_type, "chunk_index": i + 1}
                ))
 
        # FIX #3: GIU text goc cho LLM truoc khi tokenize cho BM25 (ap dung TOAN BO chunks)
        for chunk in all_chunks:
            chunk.metadata["noi_dung_goc"] = chunk.page_content
            chunk.page_content = tokenize_cached(chunk.page_content)
 
        if all_chunks:
            # Document Versioning: Xoa vector cu
            try:
                client.delete(
                    collection_name="TaiLieuKyThuat_v2",
                    points_selector=models.Filter(must=[
                        models.FieldCondition(key="metadata.file_goc", match=models.MatchValue(value=ten_file)),
                        models.FieldCondition(key="metadata.phong_ban_quyen", match=models.MatchValue(value=thu_muc))
                    ])
                )
            except Exception as e:
                logger.error(f"Khong xoa duoc vector cu cua {ten_file}: {e}", exc_info=True)
                raise
 
            vectorstore.add_documents(all_chunks)
            report["total_chunks"] += len(all_chunks)
 
    except Exception as e:
        logger.error(f"Loi doc file {ten_file}: {e}", exc_info=True)
        report["status"] = "error"
        report["message"] = str(e)
 
    report["time_taken"] = round(time.time() - start_time, 2)
    if not report["message"]:
        report["message"] = f"Da nap {report['total_chunks']} chunks tu file {ext}."
    return report