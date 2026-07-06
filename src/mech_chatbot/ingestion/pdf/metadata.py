# -*- coding: utf-8 -*-
"""Auto-split tu ingestion/pdf_processor.py (P1.3). Giu nguyen logic goc; chi tach file + import."""

import re
import json
from mech_chatbot.config.logging import logger
from mech_chatbot.llm.vision_client import describe_gemini_error, is_retryable_error

# cross-module (owned) imports
from mech_chatbot.ingestion.pdf.config import GEMINI_METADATA_MODE
from mech_chatbot.ingestion.pdf.vision import call_gemini_vision


def _metadata_needs_llm(result):
    if GEMINI_METADATA_MODE in {"off", "false", "0", "none"}:
        return False
    if GEMINI_METADATA_MODE == "always":
        return True
    
    if not result.get("ma_doi_tuong"):
        return True
        
    # Neu co nhieu ma nhung chua phan loai duoc BTP/vat tu thi nen goi LLM
    if len(result.get("ma_doi_tuong", [])) >= 2 and not result.get("ma_btp") and not result.get("ma_vat_tu"):
        return True
        
    critical_fields = ("ten_tai_lieu", "loai_tai_lieu", "vat_lieu")
    return any(str(result.get(field) or "").strip() in {"", "Khong ro"} for field in critical_fields)


def extract_metadata_smart(text, ten_file, thu_muc, vision_model=None, quality_warnings=None):
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

    ma_chinh_regex = []
    ma_lien_quan_regex = []
    
    for m in ma_doi_tuong_regex:
        if m in ten_file:
            ma_chinh_regex.append(m)
        else:
            ma_lien_quan_regex.append(m)
            
    if not ma_chinh_regex and ma_doi_tuong_regex:
        ma_chinh_regex = [ma_doi_tuong_regex[0]]
        ma_lien_quan_regex = ma_doi_tuong_regex[1:]

    result = {
        "ma_doi_tuong": ma_doi_tuong_regex,
        "ma_chinh": ma_chinh_regex,
        "ma_btp": [],
        "ma_vat_tu": [],
        "ma_lien_quan": ma_lien_quan_regex,
        "ten_tai_lieu": ten_sp_val,
        "loai_tai_lieu": "Ban ve gia cong",  # Default
        "cong_doan": cong_doan_val, "vat_lieu": vat_lieu_val, "so_luong": so_luong_val,
        "nguoi_lap": nguoi_lap_val, "ngay_ve": ngay_ve_val, "dung_sai_day": dung_sai_day,
        "dung_sai_khac": dung_sai_khac, "kich_thuoc": kich_thuoc_val,
        "yckt": yckt_text.strip(), "hdcv": hdcv_val
    }
 
    # HYBRID APPROACH: LLM Extraction de doc moi ma (V2).
    # Mac dinh chi goi Gemini khi metadata quan trong con thieu de giam rate limit.
    if vision_model and _metadata_needs_llm(result):
        prompt = f"""
        Ban la chuyen gia doc tai lieu co khi. Hay trich xuat cac thong tin sau tu doan text, tra ve dung dinh dang JSON:
            "ma_chinh": ["ma 1"],
            "ma_btp": ["ma 2"],
            "ma_vat_tu": ["ma 3"],
            "ma_lien_quan": ["ma 4"],
            "ten_tai_lieu": "Ten san pham hoac tieu de tai lieu",
            "loai_tai_lieu": "Nhan ngan gon mo ta tai lieu (VD: Ban ve gia cong, So tay ISO, Catalog...)",
            "vat_lieu": "Vat lieu de cap (neu co)"
            
        Goi y cac thong tin so bo da tim thay (hay kiem tra, mo rong hoac sua lai neu can):
        - Ma chinh: {result.get("ma_chinh", [])}
        - Ma lien quan: {result.get("ma_lien_quan", [])}
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
            
            for key in ["ma_chinh", "ma_btp", "ma_vat_tu", "ma_lien_quan"]:
                if key in llm_result and isinstance(llm_result[key], list) and llm_result[key]:
                    result[key] = [str(x) for x in llm_result[key]]
                    
            # Combine all codes to ma_doi_tuong for backward compatibility
            all_codes = result["ma_chinh"] + result["ma_btp"] + result["ma_vat_tu"] + result["ma_lien_quan"]
            result["ma_doi_tuong"] = list(dict.fromkeys(all_codes))
            
            if "ten_tai_lieu" in llm_result and llm_result["ten_tai_lieu"]:
                result["ten_tai_lieu"] = str(llm_result["ten_tai_lieu"])
            if "loai_tai_lieu" in llm_result and llm_result["loai_tai_lieu"]:
                result["loai_tai_lieu"] = str(llm_result["loai_tai_lieu"])
            if "vat_lieu" in llm_result and llm_result["vat_lieu"]:
                result["vat_lieu"] = str(llm_result["vat_lieu"])
            if "quality_warnings" in llm_result and isinstance(llm_result["quality_warnings"], list) and quality_warnings is not None:
                for w in llm_result["quality_warnings"]:
                    if w not in quality_warnings:
                        quality_warnings.append(str(w))
        except Exception as e:
            detail = describe_gemini_error(e)
            msg = f"Loi LLM Fallback boc tach metadata cho {ten_file}: {detail}"
            logger.error(msg)
            if quality_warnings is not None:
                quality_warnings.append(msg)
 
    return result

__all__ = [
    'extract_metadata_smart',
    '_metadata_needs_llm',
]
