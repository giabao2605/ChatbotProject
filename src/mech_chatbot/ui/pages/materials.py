"""P2 - Trang quan tri Tu dien ma vat tu & dong nghia (chi admin).

Thay cho viec sua tay danh sach hardcode trong code. Moi thay doi co hieu luc
ngay (registry tu refresh cache) cho ca trich xuat khi ingest lan guard RAG.
"""
import streamlit as st

from mech_chatbot.auth import service as auth
from mech_chatbot.services import (
    is_engine_ready,
    list_materials,
    upsert_material,
    delete_material,
    add_material_synonym,
    delete_material_synonym,
)
from mech_chatbot.ui.i18n import t


def run_materials():
    st.title(t("T\u1eeb \u0111i\u1ec3n m\u00e3 v\u1eadt t\u01b0 & \u0111\u1ed3ng ngh\u0129a"))
    st.caption(
        t(
            "Qu\u1ea3n tr\u1ecb danh m\u1ee5c v\u1eadt li\u1ec7u chu\u1ea9n + t\u1eeb \u0111\u1ed3ng ngh\u0129a d\u00f9ng cho tr\u00edch xu\u1ea5t & chu\u1ea9n h\u00f3a "
            "khi ingest, v\u00e0 guard ch\u1ed1ng b\u1ecba v\u1eadt li\u1ec7u trong RAG. "
            "Ch\u1ec9nh \u1edf \u0111\u00e2y c\u00f3 hi\u1ec7u l\u1ef1c ngay \u2014 kh\u00f4ng c\u1ea7n s\u1eeda code."
        )
    )

    if not auth.has_role("admin"):
        st.warning(t("Ch\u1ec9 admin m\u1edbi truy c\u1eadp \u0111\u01b0\u1ee3c trang n\u00e0y."))
        return
    if not is_engine_ready():
        st.error(t("Kh\u00f4ng k\u1ebft n\u1ed1i \u0111\u01b0\u1ee3c Database."))
        return

    # ---- Form them moi ----
    with st.expander(t("Th\u00eam v\u1eadt li\u1ec7u m\u1edbi"), expanded=False):
        with st.form("add_material"):
            c1, c2, c3 = st.columns(3)
            code = c1.text_input(t("M\u00e3 chu\u1ea9n (vd SUS304)"))
            display = c2.text_input(t("T\u00ean hi\u1ec3n th\u1ecb (vd SUS 304)"))
            category = c3.text_input(t("Nh\u00f3m (vd stainless steel)"))
            if st.form_submit_button(t("Th\u00eam"), type="primary"):
                if code.strip():
                    upsert_material(code, display or code, category or None, True)
                    st.success(t("\u0110\u00e3 th\u00eam/c\u1eadp nh\u1eadt '{code}'.", code=code))
                    st.rerun()
                else:
                    st.error(t("Ph\u1ea3i nh\u1eadp M\u00e3 chu\u1ea9n."))

    st.markdown("---")
    materials = list_materials()
    if not materials:
        st.info(t("Ch\u01b0a c\u00f3 v\u1eadt li\u1ec7u n\u00e0o. H\u00e3y th\u00eam \u1edf tr\u00ean ho\u1eb7c ch\u1ea1y migration P2 \u0111\u1ec3 seed d\u1eef li\u1ec7u g\u1ed1c."))
        return

    st.markdown("**" + t("T\u1ed5ng c\u1ed9ng: {n} v\u1eadt li\u1ec7u", n=len(materials)) + "**")
    for m in materials:
        status = t("Đang hoạt động") if m["is_active"] else t("Tạm tắt")
        header = (
            f"{status} {m['code']} \u2014 {m['display']}  \u00b7  "
            f"{m['category'] or '\u2014'}  \u00b7  {len(m['synonyms'])} " + t("\u0111\u1ed3ng ngh\u0129a")
        )
        with st.expander(header):
            with st.form(f"edit_{m['material_id']}"):
                e1, e2, e3, e4 = st.columns([2, 2, 2, 1])
                code = e1.text_input(t("M\u00e3 chu\u1ea9n"), value=m["code"], key=f"c_{m['material_id']}")
                display = e2.text_input(t("T\u00ean hi\u1ec3n th\u1ecb"), value=m["display"], key=f"d_{m['material_id']}")
                category = e3.text_input(t("Nh\u00f3m"), value=m["category"] or "", key=f"cat_{m['material_id']}")
                is_active = e4.checkbox(t("B\u1eadt"), value=m["is_active"], key=f"a_{m['material_id']}")
                b1, b2 = st.columns(2)
                if b1.form_submit_button(t("L\u01b0u thay \u0111\u1ed5i"), type="primary"):
                    upsert_material(code, display, category or None, is_active, material_id=m["material_id"])
                    st.success(t("\u0110\u00e3 l\u01b0u."))
                    st.rerun()
                if b2.form_submit_button(t("X\u00f3a v\u1eadt li\u1ec7u")):
                    delete_material(m["material_id"])
                    st.warning(t("\u0110\u00e3 x\u00f3a '{code}'.", code=m['code']))
                    st.rerun()

            st.markdown("**" + t("T\u1eeb \u0111\u1ed3ng ngh\u0129a") + "**")
            if m["synonyms"]:
                for syn in m["synonyms"]:
                    s1, s2 = st.columns([4, 1])
                    s1.write(f"\u2022 {syn['synonym']}")
                    if s2.button(t("X\u00f3a"), key=f"dels_{syn['synonym_id']}"):
                        delete_material_synonym(syn["synonym_id"])
                        st.rerun()
            else:
                st.caption("_" + t("Ch\u01b0a c\u00f3 \u0111\u1ed3ng ngh\u0129a.") + "_")
            with st.form(f"addsyn_{m['material_id']}"):
                ns = st.text_input(
                    t("Th\u00eam \u0111\u1ed3ng ngh\u0129a"),
                    key=f"ns_{m['material_id']}",
                    placeholder="vd: inox 304, ss304",
                )
                if st.form_submit_button(t("Th\u00eam \u0111\u1ed3ng ngh\u0129a")):
                    if ns.strip():
                        add_material_synonym(m["material_id"], ns)
                        st.rerun()
