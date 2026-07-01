import os
import re
import time
import streamlit as st
from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import create_ingestion_job
from mech_chatbot.ui import metadata_forms
from mech_chatbot.ui.i18n import t

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
    """Suy domain / muc mat / site mac dinh theo phong ban."""
    domain, security, site = "generic", "internal", None
    try:
        from mech_chatbot.ingestion.domain_registry import (
            resolve_domain_by_department, resolve_security_by_department,
        )
        domain = resolve_domain_by_department(dept) or "generic"
        security = resolve_security_by_department(dept) or "public"
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
    st.title(t("T\u1ea3i t\u00e0i li\u1ec7u"))
    st.caption(t("Upload file v\u00e0o h\u00e0ng \u0111\u1ee3i ingest \u0111\u1ec3 worker x\u1eed l\u00fd n\u1ec1n."))

    current_user = auth.get_current_user()
    if not (auth.has_role("uploader") or auth.has_role("admin")):
        st.error(t("B\u1ea1n kh\u00f4ng c\u00f3 quy\u1ec1n t\u1ea3i t\u00e0i li\u1ec7u."))
        return

    # P0.1: Chi cho upload vao phong ban dang ACTIVE.
    # Intersect allowed_departments cua user voi danh sach phong dang bat.
    try:
        from mech_chatbot.db.repository import list_known_departments as _list_active_depts
        _active_codes = {d["code"] for d in _list_active_depts(active_only=True)}
    except Exception:
        _active_codes = None  # fallback: khong loc neu DB loi

    allowed_departments = current_user.get("allowed_departments") or [current_user.get("department")]
    allowed_departments = [d for d in allowed_departments if d]
    if _active_codes is not None:
        allowed_departments = [d for d in allowed_departments if d in _active_codes]

    if not allowed_departments:
        if _active_codes is not None:
            st.error(
                t(
                    "T\u1ea5t c\u1ea3 ph\u00f2ng ban b\u1ea1n \u0111\u01b0\u1ee3c ph\u00e9p \u0111\u1ec1u \u0111ang t\u1ea1m d\u1eebng ho\u1ea1t \u0111\u1ed9ng. "
                    "Kh\u00f4ng th\u1ec3 upload l\u00fac n\u00e0y. Li\u00ean h\u1ec7 qu\u1ea3n tr\u1ecb \u0111\u1ec3 \u0111\u01b0\u1ee3c h\u1ed7 tr\u1ee3."
                )
            )
        else:
            st.error(
                t(
                    "T\u00e0i kho\u1ea3n c\u1ee7a b\u1ea1n ch\u01b0a \u0111\u01b0\u1ee3c g\u00e1n ph\u00f2ng ban n\u00e0o. "
                    "Kh\u00f4ng th\u1ec3 upload khi ch\u01b0a c\u00f3 ph\u00f2ng ban. Li\u00ean h\u1ec7 qu\u1ea3n tr\u1ecb \u0111\u1ec3 \u0111\u01b0\u1ee3c g\u00e1n."
                )
            )
        return

    is_admin = auth.has_role("admin")

    with st.container(border=True):
        st.subheader(t("Thông tin upload"))

        upload_mode = st.radio(
            t("Cách gán phòng ban"),
            [
                t("Một phòng ban cho cả lô"),
                t("Gán phòng ban riêng cho từng file"),
            ],
            horizontal=True,
            help=t("Dùng chế độ thứ hai khi bạn upload nhiều file thuộc nhiều phòng ban khác nhau."),
        )
        per_file_mode = upload_mode == t("Gán phòng ban riêng cho từng file")

        target_department = None
        extra_departments = []
        chosen_domain = None
        chosen_security = None
        chosen_site = None
        file_assignments = None

        if not per_file_mode:
            target_department = st.selectbox(
                t("Phòng ban chính của lô upload *"),
                allowed_departments,
                format_func=dept_label,
                help=t("Tất cả file trong lần tải này sẽ thuộc phòng ban này. Nếu file thuộc nhiều phòng khác nhau, hãy chuyển sang chế độ gán riêng từng file."),
            )

            extra_departments = st.multiselect(
                t("Chia sẻ thêm cho phòng ban khác (tùy chọn)"),
                [d for d in allowed_departments if d != target_department],
                format_func=dept_label,
                help=t("Tài liệu sẽ đọc được bởi phòng chính và các phòng được chọn thêm."),
            )

            def_domain, def_security, def_site = _resolve_defaults(target_department)

            st.markdown(
                "**" + t("Loại tài liệu (domain):") + f"** `{def_domain}` — "
                "**" + t("Mức mật mặc định:") + f"** `{def_security}`"
                + (f" — **Site:** `{def_site}`" if def_site else "")
            )

            chosen_domain = def_domain
            chosen_security = def_security
            chosen_site = def_site

            with st.expander(t("Tùy chỉnh phân loại (nâng cao)"), expanded=False):
                if is_admin:
                    sec_options = SECURITY_LEVELS
                else:
                    base_idx = SECURITY_LEVELS.index(def_security)
                    sec_options = SECURITY_LEVELS[base_idx:]
                chosen_security = st.selectbox(
                    t("Mức mật"),
                    sec_options,
                    index=sec_options.index(def_security) if def_security in sec_options else 0,
                    format_func=lambda x: t(SECURITY_LABELS.get(x, x)),
                    help=t("Mặc định theo phòng. Người thường chỉ được giữ hoặc nâng cao hơn."),
                )

                if is_admin:
                    chosen_domain = st.selectbox(
                        gloss("Domain"),
                        list(DOMAIN_LABELS.keys()),
                        index=list(DOMAIN_LABELS.keys()).index(def_domain),
                        format_func=lambda x: t(DOMAIN_LABELS.get(x, x)),
                    )

                chosen_site = (
                    st.text_input(
                        t("Site / Khu (tùy chọn)"),
                        value=def_site or "",
                    ).strip() or None
                )
        else:
            st.info(
                t(
                    "Ở chế độ này, mỗi file sẽ có phòng ban chính riêng. Domain / mức mật / site sẽ tự suy theo từng phòng khi tạo job ingest."
                )
            )

        _batch_info_placeholder = st.empty()

        uploaded_files = st.file_uploader(
            t("Kéo thả file vào đây hoặc chọn file"),
            type=sorted(ext.lstrip(".") for ext in SUPPORTED_LEARNING_EXTENSIONS),
            accept_multiple_files=True,
        )

        if uploaded_files and per_file_mode:
            _batch_info_placeholder.info(
                t("Mỗi file bên dưới sẽ tạo 1 job riêng với phòng ban bạn chọn.")
            )
            st.markdown("**" + t("Gán phòng ban cho từng file") + "**")
            file_assignments = []
            for idx, uploaded_file in enumerate(uploaded_files):
                with st.container(border=True):
                    st.write(f"**{idx + 1}. {uploaded_file.name}**")
                    _file_dept = st.selectbox(
                        t("Phòng ban chính"),
                        allowed_departments,
                        format_func=dept_label,
                        key=f"upload_file_dept_{idx}",
                    )
                    _file_extra = st.multiselect(
                        t("Chia sẻ thêm cho phòng ban khác (tùy chọn)"),
                        [d for d in allowed_departments if d != _file_dept],
                        format_func=dept_label,
                        key=f"upload_file_extra_{idx}",
                    )
                    _fdomain, _fsecurity, _fsite = _resolve_defaults(_file_dept)
                    st.caption(
                        f"{t('Mặc định')}: domain `{_fdomain}` · {t('Mức mật')}: `{_fsecurity}`"
                        + (f" · Site: `{_fsite}`" if _fsite else "")
                    )
                    file_assignments.append(
                        {
                            "target_department": _file_dept,
                            "extra_departments": _file_extra,
                        }
                    )
        elif uploaded_files and len(uploaded_files) > 1:
            _batch_info_placeholder.info(
                t(
                    "ℹ️ Đang chuẩn bị upload **{n} file** — tất cả sẽ được gán vào phòng **{dept}**. "
                    "Nếu các file thuộc nhiều phòng khác nhau, hãy chuyển sang chế độ gán riêng từng file.",
                    n=len(uploaded_files), dept=dept_label(target_department),
                )
            )

        with st.expander(
            t("Thông tin tài liệu (metadata) — nên nhập để tìm kiếm/lọc tốt hơn"),
            expanded=False,
        ):
            st.caption(
                t(
                    "Không bắt buộc, nhưng nhập sẵn giúp chatbot & người dùng lọc theo ngày hiệu lực, "
                    "số văn bản... thay vì phụ thuộc hoàn toàn vào AI. "
                    "Metadata này áp dụng cho TẤT CẢ file trong lần tải này."
                )
            )
            if per_file_mode:
                st.warning(
                    t(
                        "Bạn đang gán phòng riêng từng file nhưng metadata bên dưới vẫn dùng chung cho cả lô. "
                        "Nếu từng file có metadata rất khác nhau, hãy tách thành nhiều lần upload nhỏ hơn."
                    )
                )
                _meta_common = metadata_forms.render_common_metadata("upload_meta")
                _meta_attrs = {}
            else:
                _meta_common, _meta_attrs = metadata_forms.render_metadata_section(chosen_domain, prefix="upload_meta")
        upload_meta = metadata_forms.build_upload_meta(_meta_common, _meta_attrs)

        can_submit = bool(uploaded_files) and (per_file_mode or bool(target_department))
        submitted = st.button(
            t("Đưa vào hàng đợi xử lý"),
            type="primary", use_container_width=True, disabled=not can_submit,
        )
        if not per_file_mode and not target_department:
            st.warning(t("Bạn phải chọn phòng ban trước khi gửi."))

    if submitted:
        if not per_file_mode and not target_department:
            st.error(t("Chưa chọn phòng ban — không thể gửi."))
            return
        save_uploaded_files(
            uploaded_files, target_department, current_user,
            domain=chosen_domain, security_level=chosen_security,
            site=chosen_site,
            extra_departments=extra_departments,
            upload_meta=upload_meta,
            file_assignments=file_assignments,
        )


def save_uploaded_files(
    uploaded_files, target_department, current_user,
    domain=None, security_level=None, cong_doan=None, site=None,
    extra_departments=None, upload_meta=None, file_assignments=None,
):
    success_count = 0
    fail_count = 0
    base_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    )

    with st.status(t("Đang l��u file và tạo job ingest..."), expanded=True) as status_box:
        if file_assignments:
            st.write(
                t("Chế độ nhiều phòng ban: mỗi file sẽ được lưu và tạo job theo phòng bạn đã gán riêng.")
            )
        else:
            st.write(
                f"" + t("Phòng:") + f" **{target_department}** | Domain: **{domain}** | "
                + t("Mức mật:") + f" **{security_level}**"
                + (f" | Site: **{site}**" if site else "")
            )

        for idx, uploaded_file in enumerate(uploaded_files):
            _plan = (file_assignments[idx] if file_assignments and idx < len(file_assignments) else {}) or {}
            _target_department = _plan.get("target_department") or target_department
            _extra_departments = _plan.get("extra_departments") or extra_departments or []
            _dept_folder = safe_folder_name(_target_department)
            _upload_dir = os.path.join(base_dir, "data", "raw", _dept_folder)
            os.makedirs(_upload_dir, exist_ok=True)

            raw_name = os.path.basename(uploaded_file.name)
            safe_original_name = re.sub(r'[\/*?:"<>|]', "_", raw_name)[:180]
            safe_filename = f"{int(time.time())}_{idx}_{safe_original_name}"
            file_path = os.path.join(_upload_dir, safe_filename)
            try:
                with open(file_path, "wb") as f:
                    f.write(uploaded_file.getvalue())

                # P4.8: Per-file mode -> resolve domain/security/site theo phong cua tung file
                # thay vi de None va de worker tu suy (co the suy sai neu phong dich khac phong mac dinh).
                if file_assignments and _plan:
                    _fdomain, _fsecurity, _fsite = _resolve_defaults(_target_department)
                    _domain = _fdomain
                    _security = _fsecurity
                    _site = _fsite
                else:
                    _domain = domain
                    _security = security_level
                    _site = site

                job_id = create_ingestion_job(
                    safe_original_name, file_path, _dept_folder,
                    uploaded_by=current_user["username"],
                    domain=_domain, security_level=_security,
                    cong_doan=cong_doan, site=_site,
                    phong_ban=[_dept_folder] + [safe_folder_name(d) for d in (_extra_departments or [])],
                    upload_meta=upload_meta,
                )
                if job_id:
                    success_count += 1
                    st.write(
                        f"[{uploaded_file.name}] → JobID `{job_id}` · "
                        + t("Phòng") + f": `{_target_department}`"
                    )
                else:
                    fail_count += 1
                    st.write(
                        f"[{uploaded_file.name}] → " + t("Không tạo được job")
                        + f" · " + t("Phòng") + f": `{_target_department}`"
                    )
            except Exception as e:
                fail_count += 1
                st.write(
                    f"[{uploaded_file.name}] → " + t("Lỗi: {e}", e=e)
                    + f" · " + t("Phòng") + f": `{_target_department}`"
                )

        if fail_count == 0:
            status_box.update(
                label=t("Hoàn tất: {n}/{total} file", n=success_count, total=len(uploaded_files)),
                state="complete",
            )
        else:
            status_box.update(
                label=t("Hoàn tất nhưng có lỗi: thành công {ok}, lỗi {fail}",
                         ok=success_count, fail=fail_count),
                state="error",
            )
