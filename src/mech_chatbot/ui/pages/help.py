"""P2 - Trang Tro giup / Onboarding.

Danh cho nguoi dung phong KHONG ranh ky thuat: huong dan dat cau hoi tot,
cach doc nguon trich dan, hieu gioi han he thong, quy trinh tai tai lieu, va FAQ.
Hien thi tuy theo role. Khong phu thuoc DB -> luon hien thi duoc.

Luu y trinh bay: KHONG dung icon / emoji. Chi dung tieu de, gach dau dong, bang,
va muc mo rong (expander).
"""
import streamlit as st

from mech_chatbot.auth import service as auth
from mech_chatbot.ui.i18n import get_lang


def _role_set():
    user = auth.get_current_user() or {}
    return set(user.get("roles", []))


def run_help():
    user = auth.get_current_user() or {}
    roles = _role_set()
    name = user.get("display_name") or "ban"

    # Trang nay nhieu van ban dai -> dung nhanh giua 2 ban song ngu thay vi
    # dich tung chuoi. Ngon ngu lay tu cong tac chung tren sidebar.
    if get_lang() == "en":
        _run_help_en(name, roles)
        return

    st.title("Huong dan su dung")
    st.caption("Trang tro giup danh cho nguoi moi - khong can biet ky thuat.")

    st.markdown(
        f"Chao {name}. Day la tro ly hoi dap tai lieu noi bo cua cong ty. "
        "Ban dat cau hoi bang tieng Viet binh thuong, he thong se tim trong kho tai lieu "
        "da duoc duyet va tra loi kem nguon trich dan. He thong chi tra loi dua tren tai lieu "
        "noi bo, khong phai mot tro ly kien thuc tong quat."
    )

    # ---- 1. Bat dau nhanh ----
    st.header("1. Bat dau trong 30 giay")
    st.markdown(
        "- Vao trang **Chatbot hoi dap** o thanh ben trai.\n"
        "- Go cau hoi vao o chat, vi du: *Vat lieu cua ban ve ABC-123 la gi?*\n"
        "- Doc cau tra loi va bam vao **nguon trich dan** de mo tai lieu goc kiem chung.\n"
        "- Neu cau tra loi chua dung, bam nut phan hoi (thich / khong thich) ngay duoi cau tra loi "
        "de bao cho quan tri vien."
    )

    # ---- 2. He thong hoat dong the nao ----
    st.header("2. He thong hoat dong the nao")
    st.markdown(
        "- He thong tim cac doan tai lieu lien quan nhat den cau hoi cua ban, roi tong hop "
        "thanh cau tra loi.\n"
        "- Cau tra loi **luon dua tren tai lieu noi bo da duoc duyet**, va kem nguon de ban kiem chung.\n"
        "- He thong **khong tu bia** thong tin. Neu tai lieu khong co, he thong se bao 'khong tim thay' "
        "thay vi doan bua.\n"
        "- Ban chi nhan duoc cau tra loi tu nhung tai lieu thuoc **pham vi quyen** cua ban "
        "(phong ban, khu vuc, muc mat)."
    )

    # ---- 3. Meo dat cau hoi tot ----
    st.header("3. Meo dat cau hoi tot")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Nen lam**")
        st.markdown(
            "- Neu ro ma ban ve / ma vat tu / so van ban neu co.\n"
            "- Hoi tung y mot, ngan gon, ro rang.\n"
            "- Dung tu khoa cu the (vat lieu, dung sai, cong doan, ngay hieu luc...).\n"
            "- Neu ro phien ban neu can (vi du: 'version 2', 'ban moi nhat')."
        )
    with col2:
        st.markdown("**Nen tranh**")
        st.markdown(
            "- Cau hoi qua chung chung (vi du: 'cho toi xem tai lieu').\n"
            "- Gop nhieu cau hoi vao mot dong.\n"
            "- Viet tat kho hieu, sai chinh ta ma tai lieu.\n"
            "- Hoi cac van de ngoai pham vi tai lieu cong ty."
        )

    st.markdown("**Vi du cau hoi tot theo phong ban:**")
    st.markdown(
        "- Ky thuat / Co khi: *Dung sai lo D20 tren ban ve ABC-123 version 2 la bao nhieu?*\n"
        "- Ke toan / Mua hang: *Quy trinh tam ung cong tac phi theo quy dinh moi nhat la gi?*\n"
        "- Nhan su: *Che do nghi phep nam theo so tay nhan vien quy dinh ra sao?*\n"
        "- QC / ISO: *Tieu chi nghiem thu san pham X theo quy trinh QC hien hanh?*\n"
        "- Kho / Ke hoach: *Dinh muc ton kho toi thieu cua vat tu Y la bao nhieu?*"
    )

    st.markdown(
        "Neu he thong tra loi 'khong tim thay', hay thu dien dat lai bang tu khoa khac, "
        "kem ma tai lieu, hoac kiem tra lai phien ban. Co the tai lieu chua duoc tai len, "
        "chua duoc duyet, hoac ban chua co quyen xem."
    )

    # ---- 4. Doc va hieu cau tra loi ----
    st.header("4. Doc va hieu cau tra loi")
    st.markdown(
        "- **Nguon trich dan**: moi ket luan kem nguon dang [Nguon: ten file, Trang X, Version Y]. "
        "Hay mo nguon de doi chieu truoc khi su dung.\n"
        "- **Canh bao hieu luc**: neu tai lieu da het hieu luc hoac bi thay the, he thong se ghi ro "
        "canh bao. Hay uu tien ban con hieu luc.\n"
        "- **Mau thuan tai lieu**: neu hai tai lieu da duyet noi khac nhau, he thong se canh bao va "
        "liet ke ro tung file, khong tu chon ho ban.\n"
        "- **Nhieu phien ban**: neu ton tai nhieu version / variant, cau tra loi se tach rieng tung ban "
        "de ban so sanh.\n"
        "- **'Khong tim thay'**: nghia la tai lieu trong pham vi quyen cua ban khong co thong tin do, "
        "khong phai he thong loi."
    )

    # ---- 5. Nguon trich dan & quyen xem ----
    st.header("5. Quyen xem & muc bao mat")
    st.markdown(
        "- Ban chi thay tai lieu thuoc **phong ban / khu vuc** va **muc mat** ma ban duoc phep.\n"
        "- Cac muc mat gom: **public** (cong khai noi bo), **internal** (noi bo), "
        "**confidential** (mat - vi du luong, ho so nhan su, gia von).\n"
        "- Tai lieu chua gan muc mat duoc coi la mat, chi nguoi co quyen cao moi thay -> mac dinh an toan.\n"
        "- Neu can xem tai lieu ngoai quyen, hay lien he quan tri vien de duoc cap quyen."
    )

    # ---- 6. Gioi han cua he thong ----
    st.header("6. Nhung gi he thong KHONG lam")
    st.markdown(
        "- Khong tra loi kien thuc ngoai tai lieu noi bo (thoi su, kien thuc chung, dich thuat, "
        "viet code...).\n"
        "- Khong tu suy dien so lieu (thoi gian, chi phi, dinh muc...) neu tai lieu khong ghi ro.\n"
        "- Khong dua ra tu van phap ly / thue / nhan su vuot ngoai noi dung tai lieu.\n"
        "- Khong thay the cho phe duyet chinh thuc: voi cong viec quan trong, hay luon kiem chung nguon goc."
    )

    # ---- 7. Theo role ----
    if roles & {"uploader", "admin"}:
        st.header("7. Danh cho nguoi tai tai lieu")
        st.markdown(
            "- Vao **Tai tai lieu**, chon dung **phong ban / thu muc** va **muc mat** truoc khi tai.\n"
            "- Dat ten file ro rang, kem ma ban ve va phien ban (vi du `ABC-123_v2.pdf`).\n"
            "- Sau khi tai, theo doi tien do o trang **Tien trinh ingest**.\n"
            "- He thong ho tro PDF, anh, Word, Excel, PowerPoint, CSV va file van ban.\n"
            "- Tai dung phong ban giup he thong phan loai domain va ap muc mat chinh xac."
        )
    if roles & {"reviewer", "admin"}:
        st.header("8. Danh cho nguoi duyet")
        st.markdown(
            "- Vao **Duyet tai lieu** de kiem tra ket qua boc tach va phan loai cua AI.\n"
            "- Sua metadata (ma, phien ban, loai tai lieu, muc mat, ngay hieu luc) neu can roi bam "
            "**Duyet** hoac **Tu choi**.\n"
            "- Chi tai lieu da duyet moi duoc dua vao cau tra loi cua chatbot.\n"
            "- Trang **Kho tai lieu** cho phep loc, xem va xoa tai lieu da ingest."
        )
    if "admin" in roles:
        st.header("9. Danh cho quan tri vien")
        st.markdown(
            "- **Nguoi dung**: tao tai khoan, gan role, phong ban, khu vuc, muc mat toi da.\n"
            "- **Tu dien vat tu**: khai bao ma vat lieu va tu dong nghia (khong can sua code).\n"
            "- **Audit Log**: theo doi thao tac quan trong va truy cap tai lieu mat.\n"
            "- **Phan tich / Phan hoi**: xem cau hoi nguoi dung, ti le hai long, cau tra loi bi danh gia xau.\n"
            "- **Cau hinh**: cac thiet lap he thong."
        )

    # ---- FAQ ----
    st.header("Cau hoi thuong gap")
    with st.expander("Tai sao toi khong thay mot tai lieu?"):
        st.write("Co the tai lieu thuoc phong ban / khu vuc khac, hoac muc mat cao hon quyen cua ban, "
                 "hoac chua duoc duyet, hoac chua duoc tai len. Hay lien he quan tri vien.")
    with st.expander("Cau tra loi co chinh xac khong?"):
        st.write("He thong tra loi dua tren tai lieu noi bo da duyet va luon kem nguon. Hay mo nguon de "
                 "kiem chung truoc khi su dung cho cong viec quan trong.")
    with st.expander("Tai sao he thong tra loi 'khong tim thay' du toi biet tai lieu co ton tai?"):
        st.write("Co the tai lieu nam ngoai pham vi quyen cua ban, chua duoc duyet, hoac ban dung tu khoa "
                 "chua khop. Hay thu ma tai lieu hoac tu khoa khac, hoac lien he quan tri vien.")
    with st.expander("He thong co tu doan / tu bia thong tin khong?"):
        st.write("Khong. Neu tai lieu khong co thong tin, he thong se bao 'khong tim thay' thay vi doan. "
                 "Moi con so va ket luan deu phai truy vet duoc ve tai lieu nguon.")
    with st.expander("Tai sao cau tra loi co canh bao tai lieu het hieu luc?"):
        st.write("Tai lieu do da qua ngay hieu luc hoac da bi thay the boi ban moi. Hay uu tien ban con "
                 "hieu luc, va lien he phong ban phu trach neu can ban cap nhat.")
    with st.expander("Hai tai lieu noi khac nhau, toi nen tin cai nao?"):
        st.write("He thong se canh bao mau thuan va liet ke ro tung file, nhung khong tu chon ho ban. "
                 "Hay doi chieu nguon va hoi phong ban phu trach de xac nhan ban dung.")
    with st.expander("Toi hoi duoc nhung loai cau hoi nao?"):
        st.write("Cac cau hoi lien quan den tai lieu noi bo cong ty: thong so ky thuat, quy trinh, quy dinh, "
                 "bieu mau, dinh muc, so van ban... He thong khong tra loi kien thuc tong quat ngoai tai lieu.")
    with st.expander("Du lieu cua toi co duoc bao mat khong?"):
        st.write("He thong ap dung phan quyen theo phong ban, khu vuc va muc mat. Truy cap tai lieu mat "
                 "deu duoc ghi nhan trong Audit Log. Hay giu bao mat noi dung tai lieu mat.")
    with st.expander("Toi bao loi / gop y o dau?"):
        st.write("Dung nut phan hoi ngay duoi cau tra loi, hoac bao truc tiep cho quan tri vien / bo phan IT.")

    st.markdown("---")
    st.caption("Can ho tro them? Lien he quan tri vien he thong hoac bo phan IT.")


def _run_help_en(name, roles):
    """English version of the help page (mirrors the Vietnamese content)."""
    st.title("User guide")
    st.caption("Help page for newcomers - no technical knowledge required.")

    st.markdown(
        f"Hi {name}. This is the company's internal document assistant. "
        "Just ask questions in plain language and the system will search the approved "
        "document library and answer with citations. It only answers based on internal "
        "documents - it is not a general-purpose knowledge assistant."
    )

    # ---- 1. Quick start ----
    st.header("1. Get started in 30 seconds")
    st.markdown(
        "- Open the **Chatbot** page in the left sidebar.\n"
        "- Type your question in the chat box, e.g. *What is the material of drawing ABC-123?*\n"
        "- Read the answer and click the **citations** to open the source document and verify.\n"
        "- If the answer is not right, use the feedback buttons (like / dislike) below the answer "
        "to notify the administrators."
    )

    # ---- 2. How it works ----
    st.header("2. How the system works")
    st.markdown(
        "- It finds the document chunks most relevant to your question, then synthesizes an answer.\n"
        "- Answers are **always based on approved internal documents** and include sources you can verify.\n"
        "- The system **does not make things up**. If the documents don't contain it, it says "
        "'not found' instead of guessing.\n"
        "- You only receive answers from documents within **your permission scope** "
        "(department, area, security level)."
    )

    # ---- 3. Tips for good questions ----
    st.header("3. Tips for asking good questions")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Do**")
        st.markdown(
            "- State the drawing code / material code / document number if available.\n"
            "- Ask one point at a time, short and clear.\n"
            "- Use specific keywords (material, tolerance, process step, effective date...).\n"
            "- Specify the version if needed (e.g. 'version 2', 'latest')."
        )
    with col2:
        st.markdown("**Avoid**")
        st.markdown(
            "- Questions that are too vague (e.g. 'show me the documents').\n"
            "- Combining many questions into one line.\n"
            "- Hard-to-read abbreviations or misspellings not in the documents.\n"
            "- Asking about topics outside the company's documents."
        )

    st.markdown("**Examples of good questions by department:**")
    st.markdown(
        "- Engineering / Mechanical: *What is the tolerance of hole D20 on drawing ABC-123 version 2?*\n"
        "- Accounting / Procurement: *What is the latest travel-advance process?*\n"
        "- HR: *What is the annual leave policy per the employee handbook?*\n"
        "- QC / ISO: *What are the acceptance criteria for product X per the current QC process?*\n"
        "- Warehouse / Planning: *What is the minimum stock level for material Y?*"
    )

    st.markdown(
        "If the system answers 'not found', try rephrasing with different keywords, add the document "
        "code, or check the version. The document may not be uploaded yet, not approved yet, or "
        "outside your permission."
    )

    # ---- 4. Reading the answer ----
    st.header("4. Reading and understanding the answer")
    st.markdown(
        "- **Citations**: every conclusion comes with a source like [Source: file name, Page X, Version Y]. "
        "Open the source to cross-check before using it.\n"
        "- **Validity warnings**: if a document is expired or superseded, the system flags it clearly. "
        "Prefer the version that is still valid.\n"
        "- **Document conflicts**: if two approved documents disagree, the system warns you and lists "
        "each file, without choosing for you.\n"
        "- **Multiple versions**: if several versions / variants exist, the answer separates each one "
        "so you can compare.\n"
        "- **'Not found'**: means the documents within your permission don't contain that information - "
        "it is not a system error."
    )

    # ---- 5. Access & security ----
    st.header("5. Access & security levels")
    st.markdown(
        "- You only see documents in the **department / area** and **security level** you are allowed.\n"
        "- Security levels are: **public** (internal-public), **internal**, "
        "**confidential** (e.g. salary, HR records, cost prices).\n"
        "- Documents without a security level are treated as confidential, visible only to high-privilege "
        "users -> safe by default.\n"
        "- If you need a document outside your permission, contact an administrator for access."
    )

    # ---- 6. Limits ----
    st.header("6. What the system does NOT do")
    st.markdown(
        "- It does not answer knowledge outside internal documents (news, general knowledge, "
        "translation, coding...).\n"
        "- It does not infer figures (time, cost, quotas...) if the documents don't state them.\n"
        "- It does not give legal / tax / HR advice beyond the document content.\n"
        "- It does not replace official approval: for important work, always verify the source."
    )

    # ---- 7. Role-specific ----
    if roles & {"uploader", "admin"}:
        st.header("7. For document uploaders")
        st.markdown(
            "- Go to **Upload documents**, choose the correct **department / folder** and "
            "**security level** before uploading.\n"
            "- Name files clearly, including the drawing code and version (e.g. `ABC-123_v2.pdf`).\n"
            "- After uploading, track progress on the **Ingest progress** page.\n"
            "- The system supports PDF, images, Word, Excel, PowerPoint, CSV and text files.\n"
            "- Uploading to the right department helps classify the domain and apply the correct "
            "security level."
        )
    if roles & {"reviewer", "admin"}:
        st.header("8. For reviewers")
        st.markdown(
            "- Go to **Review documents** to check the AI's extraction and classification.\n"
            "- Edit metadata (code, version, document type, security level, effective date) if needed, "
            "then click **Approve** or **Reject**.\n"
            "- Only approved documents are used in the chatbot's answers.\n"
            "- The **Document library** page lets you filter, view and delete ingested documents."
        )
    if "admin" in roles:
        st.header("9. For administrators")
        st.markdown(
            "- **Users**: create accounts, assign roles, departments, areas, max security level.\n"
            "- **Material dictionary**: declare material codes and synonyms (no code changes needed).\n"
            "- **Audit Log**: track important actions and access to confidential documents.\n"
            "- **Analytics / Feedback**: view user questions, satisfaction rate, poorly-rated answers.\n"
            "- **Settings**: system configuration."
        )

    # ---- FAQ ----
    st.header("Frequently asked questions")
    with st.expander("Why can't I see a document?"):
        st.write("It may belong to another department / area, have a higher security level than your "
                 "permission, not be approved, or not be uploaded yet. Contact an administrator.")
    with st.expander("Are the answers accurate?"):
        st.write("Answers are based on approved internal documents and always include sources. Open the "
                 "sources to verify before using them for important work.")
    with st.expander("Why does it say 'not found' when I know the document exists?"):
        st.write("It may be outside your permission scope, not approved yet, or your keywords didn't "
                 "match. Try the document code or different keywords, or contact an administrator.")
    with st.expander("Does the system guess / fabricate information?"):
        st.write("No. If the documents don't contain it, the system says 'not found' instead of guessing. "
                 "Every number and conclusion must be traceable to a source document.")
    with st.expander("Why does an answer warn that a document is expired?"):
        st.write("That document is past its effective date or has been superseded by a newer version. "
                 "Prefer the valid version, and contact the responsible department if you need an update.")
    with st.expander("Two documents disagree - which should I trust?"):
        st.write("The system warns about conflicts and lists each file, but does not choose for you. "
                 "Cross-check the sources and ask the responsible department to confirm the correct one.")
    with st.expander("What kinds of questions can I ask?"):
        st.write("Questions related to the company's internal documents: technical specs, processes, "
                 "policies, forms, quotas, document numbers... It does not answer general knowledge "
                 "outside the documents.")
    with st.expander("Is my data secure?"):
        st.write("The system enforces permissions by department, area and security level. Access to "
                 "confidential documents is recorded in the Audit Log. Please keep confidential content secure.")
    with st.expander("Where do I report issues / give feedback?"):
        st.write("Use the feedback buttons below the answer, or report directly to an administrator / IT.")

    st.markdown("---")
    st.caption("Need more help? Contact the system administrator or IT department.")
