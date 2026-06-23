import re

MATERIAL_PATTERNS = [
    r"\bSUS\s*304\b",
    r"\bSUS\s*316\b",
    r"\bSS400\b",
    r"\bSPCC\b",
    r"\bAL\s*6061\b",
    r"\bA5052\b",
]

def extract_materials(text):
    found = []
    for pattern in MATERIAL_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            found.append({
                "type": "material",
                "value": m.group(0),
                "source_text": m.group(0),
                "confidence": 0.95,
                "extracted_by": "regex"
            })
    return found

def extract_tolerances(text):
    patterns = [
        r"±\s*\d+(?:\.\d+)?\s*(?:mm)?",
        r"\+\s*\d+(?:\.\d+)?\s*/\s*-\s*\d+(?:\.\d+)?",
        r"\b\d+(?:\.\d+)?\s*±\s*\d+(?:\.\d+)?\b",
    ]
    found = []
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            found.append({
                "type": "tolerance",
                "value": m.group(0),
                "source_text": m.group(0),
                "confidence": 0.9,
                "extracted_by": "regex"
            })
    return found

def extract_dimensions(text):
    patterns = [
        r"\b\d+(?:\.\d+)?\s*[xX×]\s*\d+(?:\.\d+)?(?:\s*[xX×]\s*\d+(?:\.\d+)?)?\s*(?:mm)?\b",
        r"\bR\s*\d+(?:\.\d+)?\b",
        r"\bØ\s*\d+(?:\.\d+)?\b",
        r"\bDIA\s*\d+(?:\.\d+)?\b",
    ]
    found = []
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            found.append({
                "type": "dimension",
                "value": m.group(0),
                "source_text": m.group(0),
                "confidence": 0.85,
                "extracted_by": "regex"
            })
    return found

def extract_mechanical_attributes(text):
    results = []
    if not text:
        return results
    results.extend(extract_materials(text))
    results.extend(extract_tolerances(text))
    results.extend(extract_dimensions(text))
    return results
