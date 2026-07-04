"""P0-3: Trang Tu dien dong nghia / viet tat theo domain (chi admin).

Chinh o day co hieu luc NGAY (RAG doc glossary tu DB, cache TTL ngan) -> tang recall
cho cac phong phi co khi ma khong can sua code / deploy lai.
"""
import streamlit as st
from mech_chatbot.auth import service as auth
from mech_chatbot.db.repository import (
    list_domain_glossary,
    upsert_glossary_term,
    set_glossary_active,
    delete_glossary_term,
)
from mech_chatbot.ui.i18n import t

DOMAIN_OPTIONS = ["generic", "mechanical", "tabular"]


def run_glossary():
    st.title(t("Từ điển đồng nghĩa / viết tắt theo domain"))
    if not auth.has_role("admin"):
        st.error(t("Chỉ admin được quản lý từ điển đồng nghĩa."))
        return
    st.caption(t(
        "Mỗi mục: một 'thuật ngữ chính' + các từ đồng nghĩa/viết tắt + cụm mở rộng. "
        "RAG sẽ tự thêm các từ này vào truy vấn để tăng recall. Chỉnh ở đây có hiệu lực ngay."
    ))

    with st.expander(t("+ Thêm mục mới"), expanded=False):
        domain = st.selectbox(t("Domain"), DOMAIN_OPTIONS, key="gl_domain")
        term = st.text_input(t("Thuật ngữ chính"), key="gl_term")
        synonyms = st.text_input(t("Từ đồng nghĩa / viết tắt (phân cách bằng dấu phẩy)"), key="gl_syn")
        expansion = st.text_input(t("Cụm mở rộng thêm (không bắt buộc)"), key="gl_exp")
        if st.button(t("Lưu mục"), type="primary", key="gl_add"):
            if not term.strip():
                st.warning(t("Nhập thuật ngữ chính."))
            else:
                syn_list = [s.strip() for s in synonyms.split(",") if s.strip()]
                out = upsert_glossary_term(term.strip(), domain, synonyms=syn_list,
                                           expansion=expansion.strip() or None)
                if out.get("ok"):
                    st.success(t("Đã lưu."))
                    st.rerun()
                else:
                    st.error(out.get("message"))

    filt_domain = st.selectbox(t("Lọc theo domain"), ["(tất cả)"] + DOMAIN_OPTIONS, key="gl_filter")
    rows = list_domain_glossary(domain=None if filt_domain == "(tất cả)" else filt_domain, active_only=False)
    if not rows:
        st.info(t("Chưa có mục nào."))
        return
    for r in rows:
        with st.container(border=True):
            active_txt = t("Đang hoạt động") if r["is_active"] else t("Tạm tắt")
            st.write(active_txt + " **" + str(r["term"]) + "** - _" + str(r["domain"]) + "_")
            if r["synonyms"]:
                st.caption(t("Đồng nghĩa:") + " " + ", ".join(r["synonyms"]))
            if r.get("expansion"):
                st.caption(t("Mở rộng:") + " " + str(r["expansion"]))
            c1, c2, c3 = st.columns([2, 2, 1])
            with c1:
                new_syn = st.text_input(t("Sửa đồng nghĩa (CSV)"), value=", ".join(r["synonyms"]), key="gsyn_" + str(r["glossary_id"]))
            with c2:
                new_exp = st.text_input(t("Sửa mở rộng"), value=r.get("expansion") or "", key="gexp_" + str(r["glossary_id"]))
            with c3:
                st.write("")
                st.write("")
                if st.button(t("Luu"), key="gsave_" + str(r["glossary_id"]), use_container_width=True):
                    syn_list = [s.strip() for s in new_syn.split(",") if s.strip()]
                    upsert_glossary_term(r["term"], r["domain"], synonyms=syn_list,
                                         expansion=new_exp.strip() or None, is_active=r["is_active"],
                                         glossary_id=r["glossary_id"])
                    st.rerun()
            b1, b2 = st.columns(2)
            with b1:
                label = t("Tat") if r["is_active"] else t("Bat")
                if st.button(label, key="gtog_" + str(r["glossary_id"]), use_container_width=True):
                    set_glossary_active(r["glossary_id"], not r["is_active"])
                    st.rerun()
            with b2:
                if st.button(t("Xoa"), key="gdel_" + str(r["glossary_id"]), use_container_width=True):
                    delete_glossary_term(r["glossary_id"])
                    st.rerun()
