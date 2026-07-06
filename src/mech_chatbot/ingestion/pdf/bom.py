# -*- coding: utf-8 -*-
"""Auto-split tu ingestion/pdf_processor.py (P1.3). Giu nguyen logic goc; chi tach file + import."""

import re
import json

# cross-module (owned) imports
from mech_chatbot.ingestion.pdf.config import remove_accents


def extract_bom_records(table, table_idx=None):
    records = []
    if not table or len(table) < 2: return records
    
    cleaned_table = []
    for row in table:
        cleaned_row = [str(cell).replace("\n", " ").strip() if cell else "" for cell in row]
        cleaned_table.append(cleaned_row)
        
    header_idx = -1
    col_map = {'ma': -1, 'ten': -1, 'vat_lieu': -1, 'sl': -1, 'ghi_chu': -1, 'unit': -1}
    
    for row_idx in range(min(5, len(cleaned_table))):
        row_norm = [remove_accents(h.lower()) for h in cleaned_table[row_idx]]
        
        has_ma_or_ten = any(kw in h for h in row_norm for kw in ['ma hang', 'ma vat tu', 'ma chi tiet', 'ma btp', 'ma tp', 'ky hieu', 'ten vat tu', 'vat tu', 'mo ta', 'ten hang', 'chi tiet', 'ten goi'])
        has_sl_or_vatlieu = any(kw in h for h in row_norm for kw in ['so luong', 'sl', 'vat lieu'])
        
        if has_ma_or_ten and has_sl_or_vatlieu:
            header_idx = row_idx
            for i, h in enumerate(row_norm):
                if any(kw in h for kw in ['ma hang', 'ma vat tu', 'ma chi tiet', 'ma btp', 'ma tp', 'ky hieu']) and col_map['ma'] == -1: col_map['ma'] = i
                elif any(kw in h for kw in ['ten vat tu', 'vat tu', 'mo ta', 'ten hang', 'chi tiet', 'ten goi']) and col_map['ten'] == -1: col_map['ten'] = i
                elif 'vat lieu' in h and col_map['vat_lieu'] == -1: col_map['vat_lieu'] = i
                elif ('so luong' in h or h == 'sl') and col_map['sl'] == -1: col_map['sl'] = i
                elif 'ghi chu' in h and col_map['ghi_chu'] == -1: col_map['ghi_chu'] = i
                elif any(kw in h for kw in ['don vi', 'dvt', 'unit']) and col_map['unit'] == -1: col_map['unit'] = i
            break
            
    if header_idx != -1:
        import json
        for row in cleaned_table[header_idx + 1:]:
            rec = {}
            if col_map['ma'] != -1 and col_map['ma'] < len(row): rec['ma_hang'] = row[col_map['ma']]
            if col_map['ten'] != -1 and col_map['ten'] < len(row): rec['ten_vat_tu'] = row[col_map['ten']]
            if col_map['vat_lieu'] != -1 and col_map['vat_lieu'] < len(row): rec['vat_lieu'] = row[col_map['vat_lieu']]
            if col_map['sl'] != -1 and col_map['sl'] < len(row): 
                try: 
                    num_str = re.sub(r'\D', '', row[col_map['sl']])
                    rec['so_luong'] = int(num_str) if num_str else None
                except: rec['so_luong'] = None
            if col_map['ghi_chu'] != -1 and col_map['ghi_chu'] < len(row): rec['ghi_chu'] = row[col_map['ghi_chu']]
            if col_map['unit'] != -1 and col_map['unit'] < len(row): rec['don_vi'] = row[col_map['unit']]
            
            rec['confidence'] = 0.9 if rec.get('ma_hang') and rec.get('so_luong') else 0.5
            rec['raw_row_json'] = json.dumps(row, ensure_ascii=False)
            rec['source_table_index'] = table_idx
            
            if rec.get('ten_vat_tu') or rec.get('ma_hang'):
                records.append(rec)
    return records

__all__ = [
    'extract_bom_records',
]
