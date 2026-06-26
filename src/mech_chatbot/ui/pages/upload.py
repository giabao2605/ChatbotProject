import os
import re
import time
import streamlit as st
from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import create_ingestion_job

SUPPORTED_LEARNING_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".csv", ".txt", ".md",
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp", ".tif", ".tiff",
}

SECURITY_LEVELS = ["public", "internal", "confidential"]
SECURITY_LABELS = {
    "public": "public - C\u00f4ng khai",
    "internal": "internal - N\u1ed9i b\u1ed9",
    "confidential": "confidential - M\u1eadt",
}
DOMAIN_LABELS = {
    "mechanical": "mechanical - T\u00e0i li\u1ec7u k\u1ef9 thu\u1eadt / b\u1ea3n v\u1ebd",
    "tabular": "tabular - B\u1ea3ng bi\u1ec3u / s\u1ed1 li\u1ec7u",
    "generic": "generic - T\u00e0i li\u1ec7u v\u0103n b\u1ea3n chung",
}


def safe_folder_name(name: str) -> str:
    name = str(name or "UNKNOWN")
    name = re.sub(r'[\\/*?:"<>|]', "_", name)
    name = name.replace("..", "_")
    return name[:100]


def _resolve_defaults(dept):
    """Suy domain / muc mat / site mac dinh theo phong ban (an toan, co fallback)."""
    domain, security, site = "generic", "internal", None
    try:
        from mech_chatbot.ingestion.domain_registry import (
            resolve_domain_by_department, resolve_security_by_department,
        )
        domain = resolve_domain_by_department(dept) or "generic"
        security = resolve_security_by_department(dept) or "internal"
    except Exception:
        pass
    try:
        from mech_chatbot.ingestion.site_registry import resolve_site_by_department
        site = resolve_site_by_department(dept)
    except Exception:
        pass
    if security not in SECURITY_LEVELS:
        security = "internal"
    if domain not in DOMAIN_LABELS:
        domain = "generic"
    return domain, security, site


def run_upload():
    st.title("T\u1ea3i t\u00e0i li\u1ec7u")
    st.caption("Upload file v\u00e0o h\u00e0ng \u0111\u1ee3i ingest \u0111\u1ec3 worker x\u1eed l\u00fd n\u1ec1n.")

    current_user = auth.get_current_user()
    if not (auth.has_role("uploader") or auth.has_role("admin")):
        st.error("B\u1ea1n kh\u00f4ng c\u00f3 quy\u1ec1n t\u1ea3i t\u00e0i li\u1ec7u.")
        return

    # Phong ma user duoc phep upload (RBAC): chi cho chon trong pham vi nay.
    allowed_departments = current_user.get("allowed_departments") or [current_user.get("department")]
    allowed_departments = [d for d in allowed_departments if d]
    if not allowed_departments:
        st.error(
            "T\u00e0i kho\u1ea3n c\u1ee7a b\u1ea1n ch\u01b0a \u0111\u01b0\u1ee3c g\u00e1n ph\u00f2ng ban n\u00e0o. "
            "Kh\u00f4ng th\u1ec3 upload khi ch\u01b0a c\u00f3 ph\u00f2ng ban. Li\u00ean h\u1ec7 qu\u1ea3n tr\u1ecb \u0111\u1ec3 \u0111\u01b0\u1ee3c g\u00e1n."
        )
        return

    is_admin = auth.has_role("admin")

    with st.container(border=True):
        st.subheader("Th\u00f4ng tin upload")

        # 1) PHONG BAN - bat buoc (luon la dropdown, gioi han trong pham vi user).
        target_department = st.selectbox(
            "Ph\u00f2ng ban (b\u1eaft bu\u1ed9c) *",
            allowed_departments,
            help="Ch\u1ec9 hi\u1ec3n c\u00e1c ph\u00f2ng b\u1ea1n \u0111\u01b0\u1ee3c ph\u00e9p. Ph\u1ea3i ch\u1ecdn m\u1edbi g\u1eedi \u0111\u01b0\u1ee3c.",
        )

        # 2) Domain / muc mat / site suy theo phong, cho chinh khi can.
        def_domain, def_security, def_site = _resolve_defaults(target_department)

        st.markdown(
            f"**Lo\u1ea1i t\u00e0i li\u1ec7u (domain):** `{def_domain}` \u2014 "
            f"**M\u1ee9c m\u1eadt m\u1eb7c \u0111\u1ecbnh:** `{def_security}`"
            + (f" \u2014 **Site:** `{def_site}`" if def_site else "")
        )

        chosen_domain = def_domain
        chosen_security = def_security
        chosen_site = def_site
        chosen_cong_doan = None

        with st.expander("T\u00f9y ch\u1ec9nh ph\u00e2n lo\u1ea1i (n\u00e2ng cao)", expanded=False):
            # Muc mat: mac dinh theo phong. Khong co quyen admin -> khong duoc HA thap.
            if is_admin:
                sec_options = SECURITY_LEVELS
            else:
                # chi cho giu nguyen hoac NANG cao hon mac dinh
                base_idx = SECURITY_LEVELS.index(def_security)
                sec_options = SECURITY_LEVELS[base_idx:]
            chosen_security = st.selectbox(
                "M\u1ee9c m\u1eadt",
                sec_options,
                index=sec_options.index(def_security) if def_security in sec_options else 0,
                format_func=lambda x: SECURITY_LABELS.get(x, x),
                help="M\u1eb7c \u0111\u1ecbnh theo ph\u00f2ng. Ng\u01b0\u1eddi th\u01b0\u1eddng ch\u1ec9 \u0111\u01b0\u1ee3c gi\u1eef ho\u1eb7c n\u00e2ng cao h\u01a1n.",
            )

            # Domain: chi admin moi nen chinh tay (it khi can).
            if is_admin:
                chosen_domain = st.selectbox(
                    "Domain",
                    list(DOMAIN_LABELS.keys()),
                    index=list(DOMAIN_LABELS.keys()).index(def_domain),
                    format_func=lambda x: DOMAIN_LABELS.get(x, x),
                )

            # Cong doan (To): chi co y nghia voi tai lieu co khi (vd phong San xuat).
            if chosen_domain == "mechanical":
                chosen_cong_doan = st.text_input(
                    "C\u00f4ng \u0111o\u1ea1n / T\u1ed5 (t\u00f9y ch\u1ecdn)",
                    value="",
                    help="V\u00ed d\u1ee5: To_Han, To_Tien_Phay... \u0110\u1ec3 tr\u1ed1ng n\u1ebfu kh\u00f4ng c\u1ea7n.",
                ).strip() or None

            # Site (tuy chon).
            chosen_site = (st.text_input(
                "Site / Khu (t\u00f9y ch\u1ecdn)",
                value=def_site or "",
            ).strip() or None)

        uploaded_files = st.file_uploader(
            "K\u00e9o th\u1ea3 file v\u00e0o \u0111\u00e2y ho\u1eb7c ch\u1ecdn file",
            type=sorted(ext.lstrip(".") for ext in SUPPORTED_LEARNING_EXTENSIONS),
            accept_multiple_files=True,
        )

        # Bat buoc: phai co phong ban VA co file moi cho submit.
        can_submit = bool(target_department) and bool(uploaded_files)
        submitted = st.button(
            "\u0110\u01b0a v\u00e0o h\u00e0ng \u0111\u1ee3i x\u1eed l\u00fd",
            type="primary", use_container_width=True, disabled=not can_submit,
        )
        if not target_department:
            st.warning("B\u1ea1n ph\u1ea3i ch\u1ecdn ph\u00f2ng ban tr\u01b0\u1edbc khi g\u1eedi.")

    if submitted:
        if not target_department:
            st.error("Ch\u01b0a ch\u1ecdn ph\u00f2ng ban \u2014 kh\u00f4ng th\u1ec3 g\u1eedi.")
            return
        save_uploaded_files(
            uploaded_files, target_department, current_user,
            domain=chosen_domain, security_level=chosen_security,
            cong_doan=chosen_cong_doan, site=chosen_site,
        )


def save_uploaded_files(uploaded_files, target_department, current_user,
                        domain=None, security_level=None, cong_doan=None, site=None):
    success_count = 0
    fail_count = 0
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
    dept_folder = safe_folder_name(target_department)
    upload_dir = os.path.join(base_dir, "data", "raw", dept_folder)
    os.makedirs(upload_dir, exist_ok=True)

    with st.status("\u0110ang l\u01b0u file v\u00e0 t\u1ea1o job ingest...", expanded=True) as status_box:
        st.write(
            f"Ph\u00f2ng: **{target_department}** | Domain: **{domain}** | "
            f"M\u1ee9c m\u1eadt: **{security_level}**"
            + (f" | C\u00f4ng \u0111o\u1ea1n: **{cong_doan}**" if cong_doan else "")
            + (f" | Site: **{site}**" if site else "")
        )
        for idx, uploaded_file in enumerate(uploaded_files):
            raw_name = os.path.basename(uploaded_file.name)
            safe_original_name = re.sub(r'[\\/*?:"<>|]', "_", raw_name)[:180]
            safe_filename = f"{int(time.time())}_{idx}_{safe_original_name}"
            file_path = os.path.join(upload_dir, safe_filename)
            try:
                with open(file_path, "wb") as f:
                    f.write(uploaded_file.getvalue())
                job_id = create_ingestion_job(
                    safe_original_name, file_path, dept_folder,
                    uploaded_by=current_user["username"],
                    domain=domain, security_level=security_level,
                    cong_doan=cong_doan, site=site, phong_ban=dept_folder,
                )
                if job_id:
                    success_count += 1
                    st.write(f"[{uploaded_file.name}] \u2192 JobID `{job_id}`")
                else:
                    fail_count += 1
                    st.write(f"[{uploaded_file.name}] \u2192 Kh\u00f4ng t\u1ea1o \u0111\u01b0\u1ee3c job")
            except Exception as e:
                fail_count += 1
                st.write(f"[{uploaded_file.name}] \u2192 L\u1ed7i: {e}")

        if fail_count == 0:
            status_box.update(label=f"Ho\u00e0n t\u1ea5t: {success_count}/{len(uploaded_files)} file", state="complete")
        else:
            status_box.update(label=f"Ho\u00e0n t\u1ea5t nh\u01b0ng c\u00f3 l\u1ed7i: th\u00e0nh c\u00f4ng {success_count}, l\u1ed7i {fail_count}", state="error")
