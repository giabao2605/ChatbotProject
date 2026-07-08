"""P0: Form metadata tong quat DONG theo domain (dung chung cho Upload / Kho tai lieu / Duyet)."""
import re
import streamlit as st

from mech_chatbot.ui.i18n import t

DOMAINS = ["mechanical", "tabular", "generic"]
DOMAIN_LABELS = {
    "mechanical": "C\u01a1 kh\u00ed / K\u1ef9 thu\u1eadt",
    "tabular": "B\u1ea3ng bi\u1ec3u / T\u00e0i ch\u00ednh",
    "generic": "H\u00e0nh ch\u00ednh / V\u0103n b\u1ea3n",
}

LANGUAGES = ["", "vi", "en", "vi+en", "other"]
EFFECTIVE_STATUSES = ["active", "draft", "expired", "superseded"]
EFFECTIVE_STATUS_LABELS = {
    "active": "\u0110ang hi\u1ec7u l\u1ef1c",
    "draft": "B\u1ea3n nh\u00e1p / d\u1ef1 th\u1ea3o",
    "expired": "H\u1ebft hi\u1ec7u l\u1ef1c",
    "superseded": "\u0110\u00e3 b\u1ecb thay th\u1ebf",
}

# Truong dac thu theo domain -> luu vao DocumentAttributes.
DOMAIN_ATTR_FIELDS = {
    "tabular": [
        ("don_vi", "\u0110\u01a1n v\u1ecb t\u00ednh / ti\u1ec1n t\u1ec7", "text"),
        ("ky_chung_tu", "K\u1ef3 / lo\u1ea1i ch\u1ee9ng t\u1eeb", "text"),
        ("tong_gia_tri", "T\u1ed5ng gi\u00e1 tr\u1ecb", "text"),
        ("doi_tac", "\u0110\u1ed1i t\u00e1c / Nh\u00e0 cung c\u1ea5p", "text"),
    ],
    "generic": [
        ("co_quan_ban_hanh", "C\u01a1 quan / Ph\u00f2ng ban ban h\u00e0nh", "text"),
        ("pham_vi_ap_dung", "Ph\u1ea1m vi \u00e1p d\u1ee5ng", "text"),
        ("linh_vuc", "L\u0129nh v\u1ef1c / ch\u1ee7 \u0111\u1ec1", "text"),
    ],
    "mechanical": [],
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def normalize_domain(domain):
    d = (domain or "generic").strip().lower()
    return d if d in DOMAINS else "generic"


def _s(v):
    if v is None:
        return ""
    return str(v).strip()


def _date_value(label, value, key):
    """O nhap ngay dang text (YYYY-MM-DD). Tra ve chuoi."""
    raw = st.text_input(t(label), value=_fmt_date(value), key=key, placeholder="YYYY-MM-DD")
    raw = _s(raw)
    if raw and not _DATE_RE.match(raw):
        st.caption("`" + t(label) + "` " + t("n\u00ean theo \u0111\u1ecbnh d\u1ea1ng YYYY-MM-DD (vd 2026-06-29). Gi\u00e1 tr\u1ecb hi\u1ec7n t\u1ea1i s\u1ebd kh\u00f4ng \u0111\u01b0\u1ee3c l\u01b0u n\u1ebfu sai \u0111\u1ecbnh d\u1ea1ng."))
    return raw


def _fmt_date(value):
    if value is None:
        return ""
    s = str(value)
    return s[:10] if _DATE_RE.match(s[:10]) else s


def render_common_metadata(prefix, defaults=None, show_header=True):
    """Render cac truong metadata DUNG CHUNG. Tra ve dict."""
    d = defaults or {}
    if show_header:
        st.markdown("**" + t("Th\u00f4ng tin t\u00e0i li\u1ec7u (d\u00f9ng chung)") + "**")
    title = st.text_input(
        t("Ti\u00eau \u0111\u1ec1 t\u00e0i li\u1ec7u"),
        value=_s(d.get("title")),
        key=f"{prefix}_title",
        help=t("T\u00ean g\u1ecdi d\u1ec5 \u0111\u1ecdc c\u1ee7a t\u00e0i li\u1ec7u (kh\u00e1c v\u1edbi t\u00ean file)."),
    )
    summary = st.text_area(
        t("T\u00f3m t\u1eaft n\u1ed9i dung"),
        value=_s(d.get("summary")),
        key=f"{prefix}_summary",
        help=t("V\u00e0i c\u00e2u m\u00f4 t\u1ea3 \u0111\u1ec3 ng\u01b0\u1eddi d\u00f9ng & chatbot hi\u1ec3u nhanh t\u00e0i li\u1ec7u n\u00f3i v\u1ec1 g\u00ec."),
    )
    tags = st.text_input(
        t("T\u1eeb kh\u00f3a (ph\u00e2n t\u00e1ch b\u1eb1ng d\u1ea5u ph\u1ea9y)"),
        value=_s(d.get("tags")),
        key=f"{prefix}_tags",
        help=t("VD: an to\u00e0n, 5S, b\u1ea3o tr\u00ec m\u00e1y CNC"),
    )
    c1, c2 = st.columns(2)
    with c1:
        doc_number = st.text_input(
            t("S\u1ed1 v\u0103n b\u1ea3n / ch\u1ee9ng t\u1eeb"),
            value=_s(d.get("doc_number")),
            key=f"{prefix}_doc_number",
        )
        issued_date = _date_value("Ng\u00e0y ban h\u00e0nh", d.get("issued_date"), key=f"{prefix}_issued_date")
        effective_date = _date_value("Ng\u00e0y hi\u1ec7u l\u1ef1c", d.get("effective_date"), key=f"{prefix}_effective_date")
    with c2:
        owner_signer = st.text_input(
            t("Ng\u01b0\u1eddi k\u00fd / ph\u1ee5 tr\u00e1ch"),
            value=_s(d.get("owner_signer")),
            key=f"{prefix}_owner_signer",
        )
        expiry_date = _date_value("Ng\u00e0y h\u1ebft hi\u1ec7u l\u1ef1c", d.get("expiry_date"), key=f"{prefix}_expiry_date")
        review_date = _date_value("Ng\u00e0y so\u00e1t x\u00e9t k\u1ebf ti\u1ebfp", d.get("review_date"), key=f"{prefix}_review_date")
    c3, c4 = st.columns(2)
    with c3:
        _lang = _s(d.get("language"))
        language = st.selectbox(
            t("Ng\u00f4n ng\u1eef"),
            LANGUAGES,
            index=LANGUAGES.index(_lang) if _lang in LANGUAGES else 0,
            format_func=lambda x: x or t("(kh\u00f4ng r\u00f5)"),
            key=f"{prefix}_language",
        )
    with c4:
        _st = _s(d.get("effective_status")) or "active"
        effective_status = st.selectbox(
            t("Tr\u1ea1ng th\u00e1i hi\u1ec7u l\u1ef1c"),
            EFFECTIVE_STATUSES,
            index=EFFECTIVE_STATUSES.index(_st) if _st in EFFECTIVE_STATUSES else 0,
            format_func=lambda x: t(EFFECTIVE_STATUS_LABELS.get(x, x)),
            key=f"{prefix}_effective_status",
        )

    def _vd(x):
        return x if (x == "" or _DATE_RE.match(x)) else ""

    return {
        "title": title, "summary": summary, "tags": tags, "doc_number": doc_number,
        "issued_date": _vd(issued_date), "effective_date": _vd(effective_date),
        "expiry_date": _vd(expiry_date), "review_date": _vd(review_date),
        "owner_signer": owner_signer, "language": language, "effective_status": effective_status,
    }


def render_domain_attributes(domain, prefix, defaults=None, show_header=True):
    """Render cac truong DAC THU theo domain. Tra ve dict {AttributeKey: value}."""
    dom = normalize_domain(domain)
    fields = DOMAIN_ATTR_FIELDS.get(dom, [])
    if not fields:
        return {}
    d = (defaults or {})
    if show_header:
        st.markdown("**" + t("Tr\u01b0\u1eddng ri\u00eang cho l\u0129nh v\u1ef1c: {domain}", domain=t(DOMAIN_LABELS.get(dom, dom))) + "**")
    out = {}
    cols = st.columns(2)
    for i, (key, label, _typ) in enumerate(fields):
        with cols[i % 2]:
            out[key] = st.text_input(t(label), value=_s(d.get(key)), key=f"{prefix}_attr_{key}")
    return out


def render_metadata_section(domain, prefix, common_defaults=None, attr_defaults=None):
    """Render ca common + domain attrs. Tra ve (common_dict, attrs_dict)."""
    common = render_common_metadata(prefix, defaults=common_defaults)
    attrs = render_domain_attributes(domain, prefix, defaults=attr_defaults)
    return common, attrs


def compact(d):
    """Bo cac gia tri rong/None."""
    out = {}
    for k, v in (d or {}).items():
        if v is None:
            continue
        if isinstance(v, str) and v.strip() == "":
            continue
        out[k] = v.strip() if isinstance(v, str) else v
    return out


def build_upload_meta(common, attrs):
    """Gop common + attrs thanh dict luu IngestionJobs.UploadMetaJson."""
    meta = compact(common)
    a = compact(attrs)
    if a:
        meta["attributes"] = a
    return meta
