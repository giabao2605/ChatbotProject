"""i18n module cho Mech Chatbot UI.

Cach dung:
    from mech_chatbot.ui.i18n import t, get_lang, set_lang, language_selector

    t("Xin chao")              -> "Hello" (khi lang=en) / "Xin chao" (khi lang=vi)
    t("Co {n} file", n=3)      -> "There are 3 files" / "Co 3 file"
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

import streamlit as st

LANGUAGES: dict[str, str] = {"vi": "Tieng Viet", "en": "English"}
DEFAULT_LANG = "vi"

_SESSION_KEY = "_mech_lang"

# ---------------------------------------------------------------------------
# Normalization helper
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Chuan hoa: NFD -> strip accents -> lower -> collapse whitespace."""
    nfd = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", stripped).strip().lower()


# ---------------------------------------------------------------------------
# Translation dictionary  (VI -> EN)
# ---------------------------------------------------------------------------

_EN: dict[str, str] = {}

# --- Phase 1: app.py, chatbot.py, dashboard.py, settings.py, help.py ---
_EN.update({
    "Mech Chatbot": "Mech Chatbot",
    "Chatbot": "Chatbot",
    "Dashboard": "Dashboard",
    "Cai dat": "Settings",
    "Huong dan": "Help",
    "Dang nhap": "Login",
    "Dang xuat": "Logout",
    "Nguoi dung": "User",
    "Xin chao": "Hello",
    "Tai lieu": "Documents",
    "Quan tri": "Admin",
    "Phan hoi": "Feedback",
    "Hang doi": "Queue",
    "Nguoi dung quan tri": "Users",
    "Ten dang nhap": "Username",
    "Mat khau": "Password",
    "Nhap ten dang nhap": "Enter username",
    "Nhap mat khau": "Enter password",
    "Dang nhap that bai. Kiem tra lai ten dang nhap va mat khau.": "Login failed. Please check your username and password.",
    "Chao mung": "Welcome",
    "Ngon ngu": "Language",
    "Luu cai dat": "Save settings",
    "Da luu": "Saved",
    "Gui": "Send",
    "Xoa lich su": "Clear history",
    "Cau hoi goi y": "Suggested questions",
    "Dang xu ly...": "Processing...",
    "Khong co ket qua": "No results found",
    "Loi he thong": "System error",
    "Phong ban": "Department",
    "Cai dat ngon ngu": "Language settings",
    "Hien thi ngon ngu": "Display language",
    "Ap dung": "Apply",
    "Huy": "Cancel",
    "Xac nhan": "Confirm",
    "Dong": "Close",
    "Them": "Add",
    "Sua": "Edit",
    "Xoa": "Delete",
    "Luu": "Save",
    "Tai len": "Upload",
    "Tai xuong": "Download",
    "Tim kiem": "Search",
    "Loc": "Filter",
    "Lam moi": "Refresh",
    "Chi tiet": "Details",
    "Tat ca": "All",
    "Khong co du lieu": "No data available",
    "Dang tai...": "Loading...",
    "Thanh cong": "Success",
    "That bai": "Failed",
    "Canh bao": "Warning",
    "Thong tin": "Information",
    "Loi": "Error",
})

# --- Phase 2: labels.py ---
_EN.update({
    "Co khi": "Mechanical",
    "Co khi / Ky thuat": "Mechanical / Engineering",
    "Bang bieu / Tai chinh": "Tabular / Financial",
    "Hanh chinh / Van ban": "Administrative / Document",
    "Dang xu ly": "Processing",
    "Cho xu ly": "Pending",
    "Da phat hanh": "Published",
    "That bai": "Failed",
    "Huy": "Cancelled",
    "Cho duyet": "Pending review",
    "Dang phat hanh": "Publishing",
    "Luu nhap": "Draft",
    "Tu choi": "Rejected",
    "Luu tru": "Archived",
    "Da thay the": "Superseded",
    "Cho thu lai": "Pending retry",
    "Cho quota": "Waiting quota",
    "Phan loai": "Classifying",
    "Trich xuat": "Extracting",
    "Nhung": "Embedding",
    "Cong khai": "Public",
    "Noi bo": "Internal",
    "Mat": "Confidential",
    "(khong ro)": "(unknown)",
    "Trang thai": "Status",
    "Ten file": "File name",
    "Phong": "Dept",
    "Trang thai hieu luc": "Effective status",
    "Het han": "Expired",
    "Sap het han": "Expiring soon",
    "Hien hanh": "Current",
    "Cu": "Old",
})

# --- Phase 2: metadata_forms.py ---
_EN.update({
    "Thong tin tai lieu (dung chung)": "Document information (common)",
    "Tieu de tai lieu": "Document title",
    "Ten goi de doc cua tai lieu (khac voi ten file).": "Human-readable document name (different from filename).",
    "Tom tat noi dung": "Content summary",
    "Vai cau mo ta de nguoi dung & chatbot hieu nhanh tai lieu noi ve gi.": "A few sentences so users & the chatbot understand the document quickly.",
    "Tu khoa (phan tach bang dau phay)": "Keywords (comma-separated)",
    "VD: an toan, 5S, bao tri may CNC": "E.g.: safety, 5S, CNC machine maintenance",
    "So van ban / chung tu": "Document number",
    "Nguoi ky / phu trach": "Signer / responsible person",
    "Ngay ban hanh": "Issue date",
    "Ngay hieu luc": "Effective date",
    "Ngay het hieu luc": "Expiry date",
    "Ngay soat xet ke tiep": "Next review date",
    "Ngon ngu": "Language",
    "(khong ro)": "(unknown)",
    "Trang thai hieu luc": "Effective status",
    "Dang hieu luc": "Active",
    "Ban nhap / du thao": "Draft",
    "Het hieu luc": "Expired",
    "Da bi thay the": "Superseded",
    "Truong rieng cho linh vuc: {domain}": "Domain-specific fields: {domain}",
    "Don vi tinh / tien te": "Unit / currency",
    "Ky / loai chung tu": "Period / document type",
    "Tong gia tri": "Total value",
    "Doi tac / Nha cung cap": "Partner / Supplier",
    "Co quan / Phong ban ban hanh": "Issuing authority / department",
    "Pham vi ap dung": "Scope of application",
    "Linh vuc / chu de": "Field / topic",
    "nen theo dinh dang YYYY-MM-DD (vd 2026-06-29). Gia tri hien tai se khong duoc luu neu sai dinh dang.": "should follow YYYY-MM-DD format (e.g. 2026-06-29). The current value will not be saved if the format is wrong.",
})

# --- Phase 2: upload.py ---
_EN.update({
    "Tai tai lieu": "Upload document",
    "Upload file vao hang doi ingest de worker xu ly nen.": "Upload files to the ingest queue for background worker processing.",
    "Ban khong co quyen tai tai lieu.": "You do not have permission to upload documents.",
    "Tai khoan cua ban chua duoc gan phong ban nao. Khong the upload khi chua co phong ban. Lien he quan tri de duoc gan.": "Your account has not been assigned to any department. Cannot upload without a department. Contact admin to be assigned.",
    "Thong tin upload": "Upload information",
    "Phong ban (bat buoc) *": "Department (required) *",
    "Chi hien cac phong ban duoc phep. Phai chon moi gui duoc.": "Only shows permitted departments. Must select one to submit.",
    "Chia se them cho phong ban khac (tuy chon)": "Also share with other departments (optional)",
    "Tai lieu se doc duoc boi phong chinh va cac phong duoc chon them.": "Document will be readable by the primary and additionally selected departments.",
    "Loai tai lieu (domain):": "Document type (domain):",
    "Muc mat mac dinh:": "Default security level:",
    "Tuy chinh phan loai (nang cao)": "Customize classification (advanced)",
    "Muc mat": "Security level",
    "Mac dinh theo phong. Nguoi thuong chi duoc giu hoac nang cao hon.": "Default per department. Regular users can only keep or increase classification.",
    "Site / Khu (tuy chon)": "Site / Zone (optional)",
    "Keo tha file vao day hoac chon file": "Drag and drop files here or click to browse",
    "Thong tin tai lieu (metadata) - nen nhap de tim kiem/loc tot hon": "Document metadata — recommended for better search/filtering",
    "Khong bat buoc, nhung nhap san giup chatbot & nguoi dung loc theo ngay hieu luc, so van ban... thay vi phu thuoc hoan toan vao AI. Metadata nay ap dung cho TAT CA file trong lan tai nay.": "Optional, but filling it helps users & the chatbot filter by effective date, document number... instead of relying entirely on AI. This metadata applies to ALL files in this upload.",
    "Dua vao hang doi xu ly": "Submit to processing queue",
    "Ban phai chon phong ban truoc khi gui.": "You must select a department before submitting.",
    "Chua chon phong ban - khong the gui.": "No department selected — cannot submit.",
    "Dang luu file va tao job ingest...": "Saving files and creating ingest jobs...",
    "Phong:": "Dept:",
    "Muc mat:": "Security:",
    "Khong tao duoc job": "Could not create job",
    "Loi: {e}": "Error: {e}",
    "Hoan tat: {n}/{total} file": "Done: {n}/{total} file(s)",
    "Hoan tat nhung co loi: thanh cong {ok}, loi {fail}": "Completed with errors: {ok} succeeded, {fail} failed",
    "theo thu muc": "by folder",
})

# --- Phase 2: queue.py ---
_EN.update({
    "phut": "min",
    "Quan Ly Tien Trinh Nap Du Lieu": "Data Ingestion Queue",
    "Xem danh sach cac file dang duoc dua vao xu ly boc tach (Worker Queue).": "View files currently being processed by the worker queue.",
    "Khong the ket noi den Database.": "Cannot connect to the database.",
    "Dang cho xu ly": "Pending",
    "TB moi job": "Avg per job",
    "Du kien xu xong": "Estimated completion",
    "Trang thai": "Status",
    "Tat ca": "All",
    "Tim file": "Search file",
    "Tong so: {n} jobs": "Total: {n} jobs",
    "uu tien:": "priority:",
    "Phong ban:": "Department:",
    "Nguoi tai len:": "Uploaded by:",
    "Muc mat:": "Security:",
    "Trang thai:": "Status:",
    "Tien do:": "Progress:",
    "Uu tien (Priority):": "Priority:",
    "nho hon = uu tien hon": "lower = higher priority",
    "Thong bao:": "Message:",
    "Dat uu tien": "Set priority",
    "Luu uu tien": "Save priority",
    "Da cap nhat uu tien.": "Priority updated.",
    "Khong cap nhat duoc uu tien.": "Could not update priority.",
    "Thu lai": "Retry",
    "Da dua lai vao hang doi!": "Re-queued successfully!",
    "Thu lai that bai.": "Retry failed.",
    "Huy job": "Cancel job",
    "Da huy job.": "Job cancelled.",
    "Khong the huy (job co the da hoan tat hoac dang publish).": "Cannot cancel (job may already be complete or publishing).",
    "Thu lai Job {jid}": "Retry Job {jid}",
    "Loi truy xuat du lieu: {e}": "Data retrieval error: {e}",
    "Hien khong co file nao trong hang doi.": "No files currently in the queue.",
    "thap": "low",
    "thuong": "normal",
})

# --- Phase 2: feedback.py ---
_EN.update({
    "Phan loai cau tra loi bi dislike de cai thien RAG va golden set.": "Classify disliked responses to improve RAG and the golden set.",
    "Ban khong co quyen xu ly feedback.": "You do not have permission to handle feedback.",
    "Khong the ket noi Database.": "Cannot connect to the database.",
    "Chi hien feedback chua xu ly": "Show only unprocessed feedback",
    "Khong co feedback can xu ly.": "No feedback to process.",
    "Bang xep hang chat luong tai lieu (P3-3)": "Document quality ranking (P3-3)",
    "Diem tinh tu like/dislike, co trong so theo vai tro nguoi danh gia va giam dan theo thoi gian; bo qua feedback da stale. Diem thap = can xem lai.": "Score based on likes/dislikes, weighted by reviewer role and decaying over time; stale feedback excluded. Low score = needs review.",
    "Tinh lai diem chat luong": "Recalculate quality scores",
    "Da tinh lai cho {n} tai lieu.": "Recalculated for {n} documents.",
    "Chua co du lieu. Hay bam nut Tinh lai diem chat luong sau khi da co like/dislike.": "No data yet. Click Recalculate quality scores after getting likes/dislikes.",
    "Hien hanh": "Current",
    "Bo kiem thu hoi quy (P3-5)": "Regression test suite (P3-5)",
    "Tap cau hoi chuan + dap an ky vong (DocID va/hoac tu khoa). Bam Chay hoi quy de doi chieu cau tra loi hien tai cua bot, phat hien hoi quy sau khi cap nhat tai lieu/cau hinh.": "Standard question set + expected answers (DocID and/or keywords). Run regression to compare current bot answers and detect regressions after document/config updates.",
    "Them cau hoi hoi quy": "Add regression question",
    "Cau hoi": "Question",
    "ExpectedDocID (tuy chon)": "ExpectedDocID (optional)",
    "Phong ban (tuy chon)": "Department (optional)",
    "Tu khoa ky vong (cach nhau bang dau phay)": "Expected keywords (comma-separated)",
    "Them cau hoi": "Add question",
    "Da them cau hoi hoi quy.": "Regression question added.",
    "Nhap noi dung cau hoi truoc.": "Please enter the question first.",
    "Dang co {active} cau active / {total} tong.": "{active} active / {total} total.",
    "Chay hoi quy ngay": "Run regression now",
    "Dang chay bo hoi quy qua engine RAG...": "Running regression batch through RAG engine...",
    "Batch {bid}: {passed}/{total} PASS (ty le {rate}%).": "Batch {bid}: {passed}/{total} PASS ({rate}% pass rate).",
    "Ket qua batch gan nhat": "Latest batch results",
    "Quan ly cau hoi hoi quy": "Manage regression questions",
    "Tat": "Off",
    "Bat": "On",
    "Bao tri & Guardrails (P3-6)": "Maintenance & Guardrails (P3-6)",
    "Don du lieu mo coi (tham chieu toi tai lieu/chat da xoa) de diem chat luong va golden set khong bi sai lech.": "Clean up orphaned records (referencing deleted documents/chats) to prevent quality scores and golden set from skewing.",
    "Don du lieu mo coi ngay": "Clean up orphaned records now",
    "Da don: ": "Cleaned: ",
    "Cau hoi": "Question",
    "Cau tra loi bot": "Bot answer",
    "Loai loi": "Error type",
    "Cau tra loi dung": "Correct answer",
    "Ghi chu reviewer": "Reviewer note",
    "Luu phan loai": "Save classification",
    "Da cap nhat feedback va luu Golden Answer.": "Feedback updated and Golden Answer saved.",
    "Da cap nhat feedback.": "Feedback updated.",
    "Xoa feedback": "Delete feedback",
    "Xac nhan xoa vinh vien feedback nay? Khong the hoan tac.": "Confirm permanent deletion of this feedback? Cannot be undone.",
    "Xac nhan xoa": "Confirm delete",
    "Da xoa feedback.": "Feedback deleted.",
})

# --- Phase 2: users.py ---
_EN.update({
    "Quan ly nguoi dung": "User management",
    "Chi admin duoc truy cap trang nay.": "Only admins can access this page.",
    "Danh sach nguoi dung": "User list",
    "Tao nguoi dung": "Create user",
    "Phong ban & Khu": "Departments & Sites",
    "Phong ban chinh:": "Primary department:",
    "(khong)": "(none)",
    "(khong gioi han)": "(no restriction)",
    "Muc mat toi da:": "Max security level:",
    "Phan quyen RBAC": "RBAC permissions",
    "Allowed sites/khu (de trong = khong gioi han)": "Allowed sites/zones (leave empty = unrestricted)",
    "Muc mat toi da": "Max security level",
    "Luu quyen": "Save permissions",
    "Da cap nhat quyen.": "Permissions updated.",
    "Loi cap nhat: {e}": "Update error: {e}",
    "Dat lai mat khau": "Reset password",
    "Mat khau moi": "New password",
    "Dat lai mat khau": "Reset password",
    "Mat khau phai co it nhat 6 ky tu.": "Password must be at least 6 characters.",
    "Da dat lai mat khau.": "Password reset.",
    "Loi dat lai mat khau: {e}": "Password reset error: {e}",
    "Ten hien thi": "Display name",
    "Phong ban chinh": "Primary department",
    "Tao user": "Create user",
    "Username va mat khau la bat buoc.": "Username and password are required.",
    "Da tao nguoi dung.": "User created.",
    "Khong tao duoc user: {e}": "Could not create user: {e}",
    "Phong ban": "Departments",
    "Ma": "Code",
    "Ten": "Name",
    "Linh vuc": "Domain",
    "Khu mac dinh": "Default site",
    "So tai lieu": "Document count",
    "Bat/Tat nhanh tung phong ban": "Quick enable/disable departments",
    "tai lieu": "documents",
    "Da tat": "Disabled",
    "Tat": "Disable",
    "Bat": "Enable",
    "Phong **{code}** dang co **{n}** tai lieu. Bam 'Tat' lan nua de xac nhan.": "Department **{code}** has **{n}** documents. Click 'Disable' again to confirm.",
    "Da tat phong {code}.": "Department {code} disabled.",
    "Cap nhat that bai.": "Update failed.",
    "Da bat phong {code}.": "Department {code} enabled.",
    "Them / cap nhat phong ban": "Add / update department",
    "Ma phong (vd: Technical)": "Dept code (e.g. Technical)",
    "Linh vuc / kieu doc (domain)": "Field / read mode (domain)",
    "Khu mac dinh": "Default site",
    "Luu phong ban": "Save department",
    "Ma phong la bat buoc.": "Department code is required.",
    "Da luu phong ban.": "Department saved.",
    "Luu phong ban that bai.": "Department save failed.",
    "Khu / Site": "Sites / Zones",
    "Them / cap nhat khu/site": "Add / update site/zone",
    "Ma khu (vd: XUONG_CO_KHI)": "Zone code (e.g. XUONG_CO_KHI)",
    "Ten khu": "Zone name",
    "Luu khu/site": "Save site/zone",
    "Ma khu la bat buoc.": "Zone code is required.",
    "Da luu khu/site.": "Site/zone saved.",
    "Luu khu/site that bai.": "Site/zone save failed.",
})

# --- Phase 2: materials.py ---
_EN.update({
    "Quan Ly Vat Tu": "Materials Management",
    "Tra cuu vat tu, BOM, cap nhat ton kho.": "Look up materials, BOMs, and update stock.",
    "Khong the ket noi Database.": "Cannot connect to the database.",
    "Tim kiem": "Search",
    "Tim theo ma/ten vat tu...": "Search by material code/name...",
    "Ket qua tim kiem": "Search results",
    "Ma vat tu": "Material code",
    "Ten vat tu": "Material name",
    "Don vi": "Unit",
    "Ton kho": "Stock",
    "Cap nhat ton kho": "Update stock",
    "Nhap so luong moi": "Enter new quantity",
    "Luu": "Save",
    "Da cap nhat.": "Updated.",
    "Loi: {e}": "Error: {e}",
    "BOM": "BOM",
    "Danh sach thanh phan": "Component list",
    "So luong": "Quantity",
    "Khong co du lieu.": "No data.",
})

# --- Phase 2: analytics.py ---
_EN.update({
    "Phan Tich & Bao Cao": "Analytics & Reports",
    "Thong ke su dung chatbot va kho tai lieu.": "Usage statistics for the chatbot and document repository.",
    "Tong so cau hoi": "Total questions",
    "Like": "Like",
    "Dislike": "Dislike",
    "Ty le hai long": "Satisfaction rate",
    "Hoat dong 7 ngay qua": "Activity last 7 days",
    "Ngay": "Date",
    "So cau hoi": "Questions",
    "Top tai lieu duoc truy van": "Top queried documents",
    "Tai lieu": "Document",
    "So lan": "Count",
    "Khong co du lieu.": "No data.",
    "Loi tai du lieu: {e}": "Data load error: {e}",
})

# --- Phase 2: audit.py ---
_EN.update({
    "Nhat Ky He Thong": "System Audit Log",
    "Lich su hanh dong cua nguoi dung va he thong.": "History of user and system actions.",
    "Chi admin duoc xem nhat ky.": "Only admins can view the audit log.",
    "Khong the ket noi Database.": "Cannot connect to the database.",
    "Thoi gian": "Time",
    "Nguoi dung": "User",
    "Hanh dong": "Action",
    "Doi tuong": "Target",
    "Chi tiet": "Details",
    "Loc theo nguoi dung": "Filter by user",
    "Loc theo hanh dong": "Filter by action",
    "Tat ca": "All",
    "Khong co ban ghi nao.": "No records found.",
    "Loi tai du lieu: {e}": "Data load error: {e}",
})

# --- Phase 2: documents.py ---
_EN.update({
    "Kho Tai Lieu": "Document Repository",
    "Danh sach tai lieu da duoc nap vao he thong.": "List of documents loaded into the system.",
    "Phong ban": "Department",
    "Muc mat": "Security",
    "Hieu luc": "Validity",
    "Tim kiem": "Search",
    "Tat ca": "All",
    "Tong so: {n} tai lieu": "Total: {n} documents",
    "Khong tim thay tai lieu nao.": "No documents found.",
    "Loi truy xuat: {e}": "Query error: {e}",
    "File:": "File:",
    "(chua xac dinh)": "(not determined)",
    "Tieu de:": "Title:",
    "So van ban:": "Document number:",
    "Nguoi ky:": "Signer:",
    "Ngon ngu:": "Language:",
    "Ngay hieu luc:": "Effective date:",
    "Ngay het han:": "Expiry date:",
    "Soat xet:": "Review date:",
    "Trang thai hieu luc:": "Effective status:",
    "Tom tat:": "Summary:",
    "Danh dau hien hanh": "Mark as current",
    "Danh dau het hieu luc": "Mark as expired",
    "Xoa tai lieu": "Delete document",
    "Xac nhan xoa vinh vien? Khong the hoan tac.": "Confirm permanent deletion? Cannot be undone.",
    "Da danh dau hien hanh.": "Marked as current.",
    "Da cap nhat trang thai.": "Status updated.",
    "Da xoa tai lieu.": "Document deleted.",
    "Loi: {e}": "Error: {e}",
    "Loi xoa: {e}": "Delete error: {e}",
    "Tai lieu sap het hieu luc / can soat xet": "Documents expiring / due for review",
    "Hien cac tai lieu co ExpiryDate hoac ReviewDate trong vong 60 ngay toi, hoac da qua han ma van dang o trang thai active.": "Shows documents with ExpiryDate or ReviewDate within 60 days, or already expired but still active.",
    "Khong co tai lieu nao sap het han trong 60 ngay toi.": "No documents expiring in the next 60 days.",
    "Ten file": "File name",
    "Phong": "Dept",
    "Trang thai": "Status",
    "Het han": "Expiry",
    "Soat xet": "Review",
    "Loi tai du lieu: {e}": "Data load error: {e}",
    "Con hieu luc": "Active",
    "Sap het han": "Expiring soon",
    "Da het han": "Expired",
    "Tat ca": "All",
})

# --- Phase 2: admin.py ---
_EN.update({
    "Quan tri he thong": "System administration",
    "Duyet tai lieu pending_review, sua metadata, va van hanh he thong.": "Review pending documents, edit metadata, and manage the system.",
    "Ban khong co quyen truy cap trang nay.": "You do not have permission to access this page.",
    "Duyet tai lieu": "Review documents",
    "Bulk action": "Bulk action",
    "Sua metadata hang loat": "Bulk metadata edit",
    "Tai lieu cho duyet": "Documents pending review",
    "Hien cac file da xu ly xong (status = pending_review), can Reviewer xac nhan truoc khi push len Qdrant.": "Shows processed files (status = pending_review) awaiting Reviewer confirmation before pushing to Qdrant.",
    "Khong co tai lieu nao can duyet hien tai.": "No documents currently awaiting review.",
    "Co {n} tai lieu cho duyet.": "{n} document(s) awaiting review.",
    "(cap nhat:": "(updated:",
    "Nguoi upload:": "Uploaded by:",
    "Phong ban:": "Department:",
    "(khong ro)": "(unknown)",
    "Xem ket qua trich xuat": "View extraction results",
    "Hanh dong": "Action",
    "Xac nhan / chinh sua metadata": "Confirm / edit metadata",
    "Ly do tu choi": "Rejection reason",
    "Xac nhan": "Confirm",
    "Da tu choi tai lieu.": "Document rejected.",
    "Da luu metadata, giu trang thai cho duyet.": "Metadata saved, kept in pending review.",
    "Da chuyen sang trang thai publishing. Worker se push len Qdrant.": "Switched to publishing status. Worker will push to Qdrant.",
    "Loi xu ly: {e}": "Processing error: {e}",
    "Bulk action tren jobs": "Bulk action on jobs",
    "Chon nhieu job cung luc de Approve, Reject, hoac xoa.": "Select multiple jobs to Approve, Reject, or Delete at once.",
    "Khong co job nao du dieu kien.": "No eligible jobs found.",
    "Da chon: {n} job": "{n} job(s) selected",
    "Approve tat ca da chon": "Approve all selected",
    "Reject tat ca da chon": "Reject all selected",
    "Xoa tat ca da chon": "Delete all selected",
    "Ket qua": "Results",
    "Xac nhan xoa {n} job? Khong the hoan tac.": "Confirm deletion of {n} job(s)? Cannot be undone.",
    "Da xoa.": "Deleted.",
    "{ok} thanh cong, {fail} that bai.": "{ok} succeeded, {fail} failed.",
    "Sua metadata hang loat": "Bulk metadata edit",
    "Loc tai lieu theo bo loc, sau do chon nhieu tai lieu va ap dung cung 1 metadata cho tat ca.": "Filter documents, then select multiple and apply the same metadata to all.",
    "Da chon: {n} tai lieu": "{n} document(s) selected",
    "Nhap metadata ap dung": "Enter metadata to apply",
    "Ap dung cho {n} tai lieu": "Apply to {n} document(s)",
    "Da cap nhat metadata: {ok} thanh cong, {fail} that bai.": "Metadata updated: {ok} succeeded, {fail} failed.",
    "Publish lam version moi (Archive ban cu cung variant)": "Publish as new version (archive old version in same variant)",
    "Publish song song nhu variant moi (Giu nguyen ban cu)": "Publish as new variant (keep old version)",
    "Publish nhu tai lieu doc lap (Standalone)": "Publish as standalone document",
    "Luu nhap / Can sua metadata": "Save as draft / needs metadata editing",
    "Tu choi (Reject)": "Reject",
})

# --- Phase 3: backend error / auth messages ---
_EN.update({
    # chatbot.py / RAG errors
    "H\u1ec7 th\u1ed1ng \u0111ang b\u1eadn ({n} request \u0111ang x\u1eed l\u00fd). Vui l\u00f2ng th\u1eed l\u1ea1i sau \u00edt gi\u00e2y.": (
        "System is busy ({n} request(s) being processed). Please retry in a moment."
    ),
    "Kh\u00f4ng k\u1ebft n\u1ed1i \u0111\u01b0\u1ee3c RAG Server t\u1ea1i {url}. Vui l\u00f2ng \u0111\u1ea3m b\u1ea3o server \u0111ang ch\u1ea1y.": (
        "Cannot connect to RAG Server at {url}. Please make sure the server is running."
    ),
    "RAG Server kh\u00f4ng ph\u1ea3n h\u1ed3i trong {n}s. H\u1ec7 th\u1ed1ng c\u00f3 th\u1ec3 \u0111ang qu\u00e1 t\u1ea3i.": (
        "RAG Server did not respond within {n}s. The system may be overloaded."
    ),
    "RAG Server l\u1ed7i (HTTP {code}): {detail}": "RAG Server error (HTTP {code}): {detail}",
    "RAG worker kh\u00f4ng tr\u1ea3 k\u1ebft qu\u1ea3. returncode={rc}. Log: {log}": (
        "RAG worker returned no result. returncode={rc}. Log: {log}"
    ),
    "RAG worker l\u1ed7i kh\u00f4ng r\u00f5": "RAG worker error (unknown)",
    "H\u1ec7 th\u1ed1ng g\u1eb7p l\u1ed7i kh\u00f4ng x\u00e1c \u0111\u1ecbnh. Vui l\u00f2ng th\u1eed l\u1ea1i.": (
        "The system encountered an unknown error. Please try again."
    ),
    # auth/service.py
    "L\u1ed7i truy v\u1ea5n: {e}": "Query error: {e}",
    "T\u00e0i kho\u1ea3n t\u1ea1m th\u1eddi b\u1ecb kh\u00f3a do \u0111\u0103ng nh\u1eadp sai qu\u00e1 {n} l\u1ea7n. "
    "Vui l\u00f2ng th\u1eed l\u1ea1i sau {m} ph\u00fat.": (
        "Account temporarily locked after {n} failed login attempts. "
        "Please try again in {m} minute(s)."
    ),
    "Sai t\u00ean \u0111\u0103ng nh\u1eadp ho\u1eb7c m\u1eadt kh\u1ea9u.": "Incorrect username or password.",
    # chatbot.py sidebar / RAG streaming
    "Tr\u1ee3 l\u00fd T\u00e0i li\u1ec7u N\u1ed9i b\u1ed9": "Internal Document Assistant",
    "Tra c\u1ee9u t\u00e0i li\u1ec7u n\u1ed9i b\u1ed9 th\u00f4ng minh": "Smart internal document lookup",
    "H\u00f4m nay": "Today",
    "H\u00f4m qua": "Yesterday",
    "C\u0169 h\u01a1n": "Older",
    "Ngu\u1ed3n tr\u00edch d\u1eabn ({n})": "Sources ({n})",
    "trang {p}": "page {p}",
    "\u0111\u1ed9 li\u00ean quan": "relevance",
    "B\u1ea1n kh\u00f4ng \u0111\u1ee7 quy\u1ec1n t\u1ea3i file g\u1ed1c c\u1ee7a ngu\u1ed3n n\u00e0y.": (
        "You do not have permission to download the original file of this source."
    ),
    "Kh\u00f4ng \u0111\u1ecdc \u0111\u01b0\u1ee3c file g\u1ed1c.": "Cannot read the original file.",
    "T\u1ea3i file g\u1ed1c": "Download original file",
    "H\u00ecnh \u1ea3nh c\u0103n c\u1ee9:": "Reference images:",
    "\u0110\u00e1nh gi\u00e1:": "Rate:",
    "Th\u00edch": "Like",
    "Kh\u00f4ng th\u00edch": "Dislike",
    "Cu\u1ed9c tr\u00f2 chuy\u1ec7n m\u1edbi": "New conversation",
    "X\u00f3a ng\u1eef c\u1ea3nh hi\u1ec7n t\u1ea1i": "Clear current context",
    "T\u00ecm ki\u1ebfm l\u1ecbch s\u1eed": "Search history",
    "L\u01b0u \u00fd": "Note",
    "Bot ch\u1ec9 tr\u1ea3 l\u1eddi d\u1ef1a tr\u00ean t\u00e0i li\u1ec7u \u0111\u00e3 \u0111\u01b0\u1ee3c n\u1ea1p v\u00e0o h\u1ec7 th\u1ed1ng.": (
        "The bot only answers based on documents loaded into the system."
    ),
    "\u0110ang t\u00ecm ki\u1ebfm trong t\u00e0i li\u1ec7u...": "Searching documents...",
    "T\u1ea3i file l\u00ean n\u1ebfu c\u1ea7n": "Upload file if needed",
    "Nh\u1eadp c\u00e2u h\u1ecfi c\u1ea7n tra c\u1ee9u (t\u00e0i li\u1ec7u, quy tr\u00ecnh, ch\u00ednh s\u00e1ch...)": (
        "Enter your question (documents, processes, policies...)"
    ),
    "Vui l\u00f2ng ph\u00e2n t\u00edch h\u00ecnh \u1ea3nh n\u00e0y.": "Please analyze this image.",
    " **T\u1eeb ch\u1ed1i quy\u1ec1n truy c\u1eadp:** B\u1ea1n hi\u1ec7n ch\u1ec9 c\u00f3 quy\u1ec1n xem (viewer). B\u1ea1n kh\u00f4ng \u0111\u01b0\u1ee3c ph\u00e9p upload file t\u00e0i li\u1ec7u (PDF, Word, Excel) v\u00e0o h\u1ec7 th\u1ed1ng. B\u1ea1n ch\u1ec9 \u0111\u01b0\u1ee3c ph\u00e9p g\u1eedi **h\u00ecnh \u1ea3nh** (.jpg, .png, .webp) \u0111\u1ec3 h\u1ecfi Chatbot. Vui l\u00f2ng th\u1eed l\u1ea1i!": (
        " **Access denied:** You currently only have viewer permission. "
        "You are not allowed to upload document files (PDF, Word, Excel). "
        "You may only send **images** (.jpg, .png, .webp) to the Chatbot. Please try again!"
    ),
    "\n\nXin l\u1ed7i, h\u1ec7 th\u1ed1ng g\u1eb7p l\u1ed7i khi sinh c\u00e2u tr\u1ea3 l\u1eddi. Vui l\u00f2ng th\u1eed l\u1ea1i.": (
        "\n\nSorry, the system encountered an error while generating the answer. Please try again."
    ),
    "X\u00f3a cu\u1ed9c tr\u00f2 chuy\u1ec7n": "Delete conversation",
    # app.py
    "Tr\u1ee3 L\u00fd T\u00e0i Li\u1ec7u N\u1ed9i B\u1ed9": "Internal Document Assistant",
    "Qu\u1ea3n tr\u1ecb d\u1eef li\u1ec7u k\u1ef9 thu\u1eadt & h\u1ecfi \u0111\u00e1p RAG": "Technical data management & RAG Q&A",
    "Xin ch\u00e0o, {name}!": "Hello, {name}!",
    "Ph\u00f2ng ban: {dept}": "Department: {dept}",
    "Role: ": "Role: ",
    "Quy\u1ec1n c\u1ee7a t\u00f4i": "My permissions",
    "Ph\u00f2ng ban \u0111\u01b0\u1ee3c xem:": "Accessible departments:",
    "(ch\u01b0a g\u00e1n)": "(not assigned)",
    "Khu / Site \u0111\u01b0\u1ee3c xem:": "Accessible sites/zones:",
    "(kh\u00f4ng gi\u1edbi h\u1ea1n)": "(unrestricted)",
    "M\u1ee9c m\u1eadt t\u1ed1i \u0111a:": "Max security level:",
    "N\u1ebfu kh\u00f4ng th\u1ea5y t\u00e0i li\u1ec7u mong \u0111\u1ee3i, h\u00e3y li\u00ean h\u1ec7 admin \u0111\u1ec3 \u0111\u01b0\u1ee3c c\u1ea5p th\u00eam quy\u1ec1n.": (
        "If you don't see expected documents, contact admin to request additional permissions."
    ),
    "\u0110i\u1ec1u h\u01b0\u1edbng": "Navigation",
    "\u0110\u0103ng xu\u1ea5t": "Logout",
    "T\u00e0i kho\u1ea3n ch\u01b0a \u0111\u01b0\u1ee3c g\u00e1n quy\u1ec1n truy c\u1eadp trang n\u00e0o.": (
        "Your account has not been assigned access to any page."
    ),
    # page labels
    "T\u1ed5ng quan": "Overview",
    "Chatbot h\u1ecfi \u0111\u00e1p": "Chatbot Q&A",
    "H\u01b0\u1edbng d\u1eabn": "Help",
    "T\u1ea3i t\u00e0i li\u1ec7u": "Upload documents",
    "Ti\u1ebfn tr\u00ecnh ingest": "Ingest queue",
    "Duy\u1ec7t t\u00e0i li\u1ec7u": "Review documents",
    "Kho t\u00e0i li\u1ec7u": "Document repository",
    "Feedback Loop": "Feedback Loop",
    "Ng\u01b0\u1eddi d\u00f9ng": "Users",
    "T\u1eeb \u0111i\u1ec3n v\u1eadt t\u01b0": "Materials dictionary",
    "B\u00e1o c\u00e1o s\u1eed d\u1ee5ng": "Usage reports",
    "Audit Log": "Audit Log",
    "C\u1ea5u h\u00ecnh": "Configuration",
    "H\u1ecfi b\u1ea5t k\u1ef3 c\u00e2u h\u1ecfi n\u00e0o v\u1ec1 t\u00e0i li\u1ec7u, quy tr\u00ecnh, ch\u00ednh s\u00e1ch hay d\u1eef li\u1ec7u c\u1ee7a c\u00e1c ph\u00f2ng ban...": (
        "Ask any question about documents, processes, policies or departmental data..."
    ),
})

# --- Phase 4: dashboard.py ---
_EN.update({
    "Tổng quan hệ thống": "System overview",
    "Theo dõi nhanh trạng thái tài liệu, ingest, chatbot và feedback.": (
        "Quick overview of document, ingest, chatbot and feedback status."
    ),
    "Chỉ admin được xem trang tổng quan.": "Only admins can view the overview page.",
    "Không thể kết nối Database.": "Cannot connect to the database.",
    "Tổng tài liệu": "Total documents",
    "Chờ duyệt": "Pending review",
    "Job đang xử lý": "Running jobs",
    "Job lỗi": "Failed jobs",
    "Đã published": "Published",
    "Chat hôm nay": "Chats today",
    "Feedback cần xử lý": "Feedback to process",
    "Thống kê theo phòng ban": "Statistics by department",
    "Tài liệu mới": "Recent documents",
    "Job lỗi gần đây": "Recent failed jobs",
    "Chưa có dữ liệu theo phòng ban.": "No departmental data yet.",
    "Phong ban": "Department",
    "Tai lieu so huu": "Owned documents",
    "Tai lieu duoc chia se": "Shared documents",
    "Tong tai lieu": "Total documents",
    "Da publish": "Published",
    "Cho duyet": "Pending review",
    "Mat (confidential)": "Confidential",
    "Job dang chay": "Running jobs",
    "Job loi": "Failed jobs",
    "Co {n} job ingest dang loi. Phong can chu y: {depts} - kiem tra tab Hang doi.": (
        "{n} ingest job(s) failing. Departments to check: {depts} — see the Queue tab."
    ),
    "Khong tai duoc thong ke theo phong: {e}": "Cannot load department stats: {e}",
    "Khong tai duoc tai lieu moi: {e}": "Cannot load recent documents: {e}",
    "Chưa có tài liệu.": "No documents yet.",
    "Khong tai duoc job loi: {e}": "Cannot load failed jobs: {e}",
    "Không có job lỗi.": "No failed jobs.",
    "Phòng ban:": "Department:",
    "Cập nhật:": "Updated:",
    "Không có thông báo lỗi.": "No error message.",
    "Phòng ban": "Department",
    "Số tài liệu": "Documents",
})

# --- Phase 4: settings.py ---
_EN.update({
    "Cấu hình hệ thống": "System configuration",
    "Chỉ admin được truy cập cấu hình.": "Only admins can access configuration.",
    "Kiểm tra sức khỏe hệ thống": "System health check",
    "Kiểm tra nhanh kết nối tới Database, Qdrant, Embedding và LLM.": (
        "Quick connectivity check for Database, Qdrant, Embedding and LLM."
    ),
    "Chạy kiểm tra ngay": "Run checks now",
    "Qdrant (vector DB)": "Qdrant (vector DB)",
    "Embedding model": "Embedding model",
    "Thống kê hệ thống": "System statistics",
    "Tổng số tài liệu": "Total documents",
    "Số phòng ban có tài liệu": "Departments with documents",
    "Tài liệu theo phòng ban": "Documents by department",
    "Không lấy được thống kê: {e}": "Cannot load statistics: {e}",
    "Tham số cấu hình ứng dụng": "Application settings",
    "Không đọc được cấu hình: {e}": "Cannot read configuration: {e}",
    "Số ngày cảnh báo trước khi tài liệu hết hiệu lực": "Days to warn before document expiry",
    "Tài liệu có ExpiryDate trong khoảng này sẽ được cảnh báo 'sắp hết hạn'.": (
        "Documents with ExpiryDate within this range will be flagged as 'expiring soon'."
    ),
    "Số đoạn tài liệu tối đa khi tìm kiếm chung (RAG general top_k)": (
        "Max document chunks for general search (RAG general top_k)"
    ),
    "Số chunk tối đa lấy khi câu hỏi không gắn mã tài liệu cụ thể.": (
        "Max chunks retrieved when the question has no specific document code."
    ),
    "Lưu cấu hình": "Save configuration",
    "Đã lưu cấu hình ứng dụng.": "Application configuration saved.",
    "Lỗi lưu cấu hình: {e}": "Configuration save error: {e}",
    "Đang dùng RAG Server API": "Using RAG Server API",
    "Chưa có RAG_SERVER_URL. Hệ thống sẽ dùng subprocess worker.": (
        "No RAG_SERVER_URL configured. System will use subprocess worker."
    ),
    "Bảo mật": "Security",
    "Trang này không hiển thị API key, password database hoặc token.": (
        "This page does not display API keys, database passwords or tokens."
    ),
    "Engine chưa khởi tạo.": "Engine not initialized.",
    "Kết nối Database OK.": "Database connection OK.",
    "Lỗi: {e}": "Error: {e}",
    "Chưa cấu hình QDRANT_URL.": "QDRANT_URL not configured.",
    "Qdrant OK ({url}).": "Qdrant OK ({url}).",
    "Qdrant trả về HTTP {code}.": "Qdrant returned HTTP {code}.",
    "Lỗi kết nối Qdrant: {e}": "Qdrant connection error: {e}",
    "Embedding: {model} (dim={dim}).": "Embedding: {model} (dim={dim}).",
    "Chưa cấu hình EMBEDDING_MODEL.": "EMBEDDING_MODEL not configured.",
    "Đã cấu hình API key cho LLM: ": "LLM API key configured: ",
    "Chưa thấy API key LLM trong môi trường (OPENAI/GOOGLE/GEMINI/ANTHROPIC/LLM_API_KEY).": (
        "No LLM API key found in environment (OPENAI/GOOGLE/GEMINI/ANTHROPIC/LLM_API_KEY)."
    ),
})

# --- Phase 4: labels.py status badges ---
_EN.update({
    "Chờ duyệt": "Pending review",
    "Đã duyệt": "Approved",
    "Đã xuất bản": "Published",
    "Bản nháp": "Draft",
    "Từ chối": "Rejected",
    "Lưu trữ": "Archived",
    "Đã thay thế": "Superseded",
    "Đang chờ": "Pending",
    "Chờ thử lại": "Pending retry",
    "Đang phân loại": "Classifying",
    "Đang bóc tách": "Extracting",
    "Đang tạo vector": "Embedding",
    "Đang xuất bản": "Publishing",
    "Lỗi": "Error",
    "Chờ quota": "Waiting quota",
    "Đã hủy": "Cancelled",
    "Bị chặn (chất lượng)": "Blocked (quality)",
    "Đạt": "Passed",
    "Cảnh báo": "Warning",
    "(không rõ)": "(unknown)",
})

# --- Phase 4: RAG chatbot response messages (used in rag/service.py) ---
_EN.update({
    # Chitchat greeting
    "Chào bạn! Mình là trợ lý AI kỹ thuật cơ khí. Bạn có thể hỏi mình về bản vẽ, "
    "dung sai, vật liệu, quy trình gia công hoặc upload tài li���u để mình học thêm.": (
        "Hi there! I'm the AI technical assistant. You can ask me about drawings, "
        "tolerances, materials, manufacturing processes or upload documents for me to learn."
    ),
    # Version disambiguation
    "Bạn muốn so sánh tài liệu này với phiên bản nào? (Ví dụ: v1 và v2, hoặc bản "
    "đang lưu hành và bản bị lưu trữ gần nhất). Vui lòng chỉ định rõ phiên bản để "
    "mình đối chiếu số liệu chính xác nhé.": (
        "Which versions would you like to compare? (For example: v1 and v2, or the "
        "current version vs. the most recent archived one). Please specify the versions "
        "so I can cross-reference the data accurately."
    ),
    # No docs for exact code
    "Rất tiếc, mình không tìm thấy mã số '{codes}' nào trong hệ thống bản vẽ hiện tại. "
    "Vui lòng kiểm tra lại mã hoặc mô tả rõ hơn.": (
        "Sorry, I couldn't find the code '{codes}' in the current drawing system. "
        "Please double-check the code or provide more details."
    ),
    # Empty context
    "Tài liệu hiện tại chưa có dữ liệu liên quan đến câu hỏi của bạn. "
    "Mình không thể trả lời dựa trên suy đoán. "
    "Vui lòng nạp tài liệu vào hệ thống trước, hoặc hỏi nội dung đã có trong dữ liệu.": (
        "The current documents do not contain data related to your question. "
        "I cannot answer based on guesswork. "
        "Please load the relevant documents into the system first, or ask about existing data."
    ),
    # Empty context (post-rerank)
    "Tài liệu hiện tại không ghi chú thông tin về câu hỏi của bạn. "
    "Vui lòng kiểm tra lại hoặc cung cấp thêm bản vẽ.": (
        "The current documents do not contain information about your question. "
        "Please check again or provide additional drawings."
    ),
    # Variant ambiguity
    "Mình tìm thấy nhiều tài liệu có thể khớp với mô tả của bạn. "
    "Bạn muốn tra theo tài liệu nào dưới đây?": (
        "I found multiple documents that may match your description. "
        "Which document below would you like me to look up?"
    ),
    "Bạn có thể trả lời bằng mã/model ở cột đầu, hoặc yêu cầu 'so sánh các model' "
    "để mình lập bảng đối chiếu.": (
        "You can reply with the code/model from the first column, or ask me to "
        "'compare models' for a side-by-side comparison."
    ),
    # Insufficient candidate
    "Mình chưa xác định chắc chắn được tài liệu/bản vẽ cần tra theo mô tả của bạn. "
    "Bạn vui lòng cung cấp thêm mã bản vẽ, model, tên sản phẩm, kích thước hoặc "
    "vật liệu cụ thể hơn nhé.": (
        "I couldn't determine the exact document/drawing from your description. "
        "Could you please provide a drawing code, model name, product name, "
        "dimensions or material for a more specific lookup?"
    ),
    # Source citations header
    "Nguon tham chieu:": "References:",
})

# --- Phase 5: cac chuoi f-string con dang do (users/documents/queue/upload/chatbot) ---
_EN.update({
    # documents.py
    "Chọn DocID {doc_id} · {name}": "Select DocID {doc_id} · {name}",
    # queue.py
    "Chọn Job {job_id} · {name}": "Select Job {job_id} · {name}",
    # upload.py
    "ℹ️ Đang chuẩn bị upload **{n} file** — tất cả sẽ được gán vào phòng **{dept}**. "
    "Nếu các file thuộc nhiều phòng khác nhau, hãy chuyển sang chế độ gán riêng từng file.": (
        "ℹ️ Preparing to upload **{n} file(s)** — all will be assigned to department **{dept}**. "
        "If the files belong to different departments, switch to per-file assignment mode."
    ),
    # users.py
    "{n_docs} tài liệu · {n_users} user": "{n_docs} docs · {n_users} users",
    " · {n} shared": " · {n} shared",
    "{n_jobs} job pending": "{n_jobs} pending job(s)",
    "⚠️ Xác nhận tắt phòng **{code}**? "
    "Hiện có **{n_docs}** tài liệu, **{n_users}** user, **{n_jobs}** job pending. "
    "Upload mới sẽ bị khóa. Bấm **Tắt** lần nữa để xác nhận.": (
        "⚠️ Confirm disabling department **{code}**? "
        "There are **{n_docs}** document(s), **{n_users}** user(s), **{n_jobs}** pending job(s). "
        "New uploads will be locked. Click **Disable** again to confirm."
    ),
    "📦 Xác nhận archive phòng **{code}**? "
    "Điều kiện: 0 user, 0 job pending. Hiện tại có **{n_users}** user, **{n_jobs}** job pending, **{n_docs}** tài liệu. "
    "Bấm **Archive** lần nữa để xác nhận.": (
        "📦 Confirm archiving department **{code}**? "
        "Requirements: 0 users, 0 pending jobs. Currently there are **{n_users}** user(s), **{n_jobs}** pending job(s), **{n_docs}** document(s). "
        "Click **Archive** again to confirm."
    ),
    "Đã archive phòng {code}.": "Department {code} archived.",
    "♻️ Xac nhan khoi phuc phong **{code}** tu trang thai archived? "
    "Phong se chuyen ve 'disabled' (chua nhan job/user moi). "
    "Bam **Khoi phuc** lan nua de xac nhan.": (
        "♻️ Confirm restoring department **{code}** from archived status? "
        "The department will move back to 'disabled' (won't accept new jobs/users). "
        "Click **Restore** again to confirm."
    ),
    "Đã chuyển **{docs}** tài liệu và **{users}** user từ **{src}** sang **{dst}**.": (
        "Moved **{docs}** document(s) and **{users}** user(s) from **{src}** to **{dst}**."
    ),
    "Có {n} DocID chưa đồng bộ được Qdrant: {ids}": (
        "{n} DocID(s) not yet synced to Qdrant: {ids}"
    ),
    # chatbot.py (luong hoc tai lieu)
    "Hay hoc cac tai lieu nay: **{files}**": "Please learn these documents: **{files}**",
    "---\n**[{i}/{total}] Đang lưu file: {name}**": "---\n**[{i}/{total}] Saving file: {name}**",
    "Đã đưa {n} tài liệu vào hàng đợi (Queue)...": "Queued {n} document(s) for processing...",
})

# --- Phase 6: bo sung day du cac chuoi con sot (admin/analytics/audit/documents/
#     feedback/materials/queue/upload/users/metadata_forms/labels/app) ---
_EN.update({
    # --- Chung / dung nhieu noi ---
    "(chưa xác định)": "(undetermined)",
    "Không kết nối được Database.": "Could not connect to the database.",
    "Không thể kết nối đến Database.": "Could not connect to the database.",
    "Chỉ admin mới truy cập được trang này.": "Only admins can access this page.",
    "Chỉ admin được truy cập trang này.": "Only admins can access this page.",
    "Mặc định": "Default",
    "Hành động": "Action",
    "Loại tài liệu": "Document type",
    # --- labels.py: field labels theo domain ---
    "Mã đối tượng": "Object code",
    "Tên sản phẩm": "Product name",
    "Vật liệu": "Material",
    "Kích thước tổng thể": "Overall dimensions",
    "Tiêu đề chứng từ": "Voucher title",
    "Đơn vị": "Unit",
    "Tiêu đề tài liệu": "Document title",
    "Loại văn bản": "Document type",
    "Số văn bản": "Document number",
    "Người ký": "Signer",
    # --- metadata_forms.py ---
    "Đơn vị tính / tiền tệ": "Unit of measure / currency",
    "Đối tác / Nhà cung cấp": "Partner / Supplier",
    "Lĩnh vực / chủ đề": "Field / topic",
    "Tên gọi dễ đọc của tài liệu (khác với tên file).": "A human-readable name for the document (different from the file name).",
    "Vài câu mô tả để người dùng & chatbot hiểu nhanh tài liệu nói về gì.": "A few sentences describing what the document is about, so users & the chatbot can grasp it quickly.",
    "nên theo định dạng YYYY-MM-DD (vd 2026-06-29). Giá trị hiện tại sẽ không được lưu nếu sai định dạng.": "should follow the YYYY-MM-DD format (e.g. 2026-06-29). The current value will not be saved if the format is wrong.",
    # --- app.py nav ---
    "Vòng đời từng phòng ban": "Per-department lifecycle",
    # --- upload.py ---
    "Upload file vào hàng đợi ingest để worker xử lý nền.": "Upload files to the ingest queue for background worker processing.",
    "Cách gán phòng ban": "Department assignment mode",
    "Một phòng ban cho cả lô": "One department for the whole batch",
    "Gán phòng ban riêng cho từng file": "Assign a separate department per file",
    "Dùng chế độ thứ hai khi bạn upload nhiều file thuộc nhiều phòng ban khác nhau.": "Use the second mode when you upload multiple files belonging to different departments.",
    "Phòng ban chính của lô upload *": "Primary department for this upload batch *",
    "Tất cả file trong lần tải này sẽ thuộc phòng ban này. Nếu file thuộc nhiều phòng khác nhau, hãy chuyển sang chế độ gán riêng từng file.": "All files in this upload will belong to this department. If files belong to different departments, switch to per-file assignment mode.",
    "Tài liệu sẽ đọc được bởi phòng chính và các phòng được chọn thêm.": "The document will be readable by the primary department and the additionally selected departments.",
    "Mức mật mặc định:": "Default security level:",
    "Mặc định theo phòng. Người thường chỉ được giữ hoặc nâng cao hơn.": "Defaults to the department. Regular users can only keep it or raise it higher.",
    "Ở chế độ này, mỗi file sẽ có phòng ban chính riêng. Domain / mức mật / site sẽ tự suy theo từng phòng khi tạo job ingest.": "In this mode, each file has its own primary department. Domain / security level / site will be inferred per department when the ingest job is created.",
    "Kéo thả file vào đây hoặc chọn file": "Drag and drop files here or browse",
    "Mỗi file bên dưới sẽ tạo 1 job riêng với phòng ban bạn chọn.": "Each file below will create its own job with the department you choose.",
    "Gán phòng ban cho từng file": "Assign a department to each file",
    "Thông tin tài liệu (metadata) — nên nhập để tìm kiếm/lọc tốt hơn": "Document information (metadata) — recommended for better search/filtering",
    "Bạn đang gán phòng riêng từng file nhưng metadata bên dưới vẫn dùng chung cho cả lô. Nếu từng file có metadata rất khác nhau, hãy tách thành nhiều lần upload nhỏ hơn.": "You are assigning departments per file, but the metadata below still applies to the whole batch. If files have very different metadata, split them into smaller uploads.",
    "Đưa vào hàng đợi xử lý": "Add to processing queue",
    "Chưa chọn phòng ban — không thể gửi.": "No department selected — cannot submit.",
    "Chế độ nhiều phòng ban: mỗi file sẽ được lưu và tạo job theo phòng bạn đã gán riêng.": "Multi-department mode: each file will be saved and queued according to the department you assigned to it.",
    "Đang lưu file và tạo job ingest...": "Saving files and creating ingest jobs...",
    "Tất cả phòng ban bạn được phép đều đang tạm dừng hoạt động. Không thể upload lúc này. Liên hệ quản trị để được hỗ trợ.": "All departments you are allowed to use are currently suspended. You cannot upload right now. Contact an administrator for help.",
    "Tài khoản của bạn chưa được gán phòng ban nào. Không thể upload khi chưa có phòng ban. Liên hệ quản trị để được gán.": "Your account is not assigned to any department. You cannot upload without a department. Contact an administrator to be assigned.",
    "Không tạo được job": "Could not create job",
    "public - Công khai": "public - Public",
    "internal - Nội bộ": "internal - Internal",
    "confidential - Mật": "confidential - Confidential",
    "mechanical - Tài liệu kỹ thuật / bản vẽ": "mechanical - Technical document / drawing",
    "tabular - Bảng biểu / số liệu": "tabular - Table / figures",
    "generic - Tài liệu văn bản chung": "generic - General text document",
    # --- queue.py ---
    "Xem danh sách các file đang được đưa vào xử lý bóc tách (Worker Queue).": "View the files being processed for extraction (Worker Queue).",
    "Đang chờ xử lý": "Pending",
    "Hiện không có file nào trong hàng đợi.": "There are currently no files in the queue.",
    "Chọn tất cả jobs đang hiển thị": "Select all displayed jobs",
    "Đã chọn {n} job.": "Selected {n} jobs.",
    "Xác nhận xóa {n} job?": "Confirm deleting {n} jobs?",
    "Xóa tất cả jobs đã chọn": "Delete all selected jobs",
    "Tiến độ:": "Progress:",
    "Đặt ưu tiên": "Set priority",
    "Đã cập nhật ưu tiên.": "Priority updated.",
    "Không cập nhật được ưu tiên.": "Could not update priority.",
    "Đã hủy job.": "Job canceled.",
    "Không thể hủy (job có thể đã hoàn tất hoặc đang publish).": "Cannot cancel (the job may already be completed or publishing).",
    "Đã đưa lại vào hàng đợi!": "Re-queued!",
    # --- admin.py (review) ---
    "Hiển các file đã xử lý xong (status = pending_review), cần Reviewer xác nhận trước khi push lên Qdrant.": "Shows files that finished processing (status = pending_review) and need reviewer approval before being pushed to Qdrant.",
    "Chọn nhiều job cùng lúc để publish, reject hoặc xóa. Publish sẽ chạy trực tiếp, không còn kẹt ở trạng thái publishing.": "Select multiple jobs at once to publish, reject or delete. Publish runs directly, no longer stuck in the publishing state.",
    "Chọn tất cả jobs đang hiển thị ": "Select all displayed jobs ",
    "Kiểu publish hàng loạt": "Bulk publish mode",
    "Lọc tài liệu theo bộ lọc, sau đó chọn nhiều tài liệu và áp dụng cùng 1 metadata cho tất cả.": "Filter documents, then select multiple and apply the same metadata to all of them.",
    "Đã xuất bản tài liệu. Chatbot có thể dùng sau khi payload Qdrant cập nhật.": "Document published. The chatbot can use it after the Qdrant payload updates.",
    "Không có job nào đủ điều kiện.": "No eligible jobs.",
    "Publish tất cả đã chọn": "Publish all selected",
    "Reject tất cả đã chọn": "Reject all selected",
    "Xóa tất cả đã chọn": "Delete all selected",
    "Xác nhận xóa {n} job/tài liệu? Không thể hoàn tác.": "Confirm deleting {n} jobs/documents? This cannot be undone.",
    "Không có tài liệu nào.": "No documents.",
    "Đã từ chối tài liệu.": "Document rejected.",
    "Đã lưu metadata, giữ trạng thái chờ duyệt.": "Metadata saved, kept in pending-review status.",
    "Đã chọn: {n} job": "Selected: {n} jobs",
    "Publish như tài liệu độc lập": "Publish as a standalone document",
    "Publish song song như variant mới": "Publish in parallel as a new variant",
    "Publish làm version mới": "Publish as a new version",
    "Publish: {ok} thành công, {fail} thất bại.": "Publish: {ok} succeeded, {fail} failed.",
    "Reject: {ok} thành công, {fail} thất bại.": "Reject: {ok} succeeded, {fail} failed.",
    "Đã chọn: {n} tài liệu": "Selected: {n} documents",
    "Đã cập nhật metadata: {ok} thành công, {fail} thất bại.": "Metadata updated: {ok} succeeded, {fail} failed.",
    "Đã xóa: {ok} thành công, {fail} thất bại.": "Deleted: {ok} succeeded, {fail} failed.",
    "Publish làm version mới (Archive bản cũ cùng variant)": "Publish as a new version (archive the old one in the same variant)",
    "Publish song song như variant mới (Giữ nguyên bản cũ)": "Publish in parallel as a new variant (keep the old one)",
    "Publish như tài liệu độc lập (Standalone)": "Publish as a standalone document (Standalone)",
    "Lưu nháp / Cần sửa metadata": "Save draft / needs metadata edits",
    "Từ chối (Reject)": "Reject",
    "Không tìm thấy DocID tương ứng với job này.": "No DocID found for this job.",
    "Publish thất bại: không tìm thấy tài liệu hoặc không update được Qdrant.": "Publish failed: document not found or Qdrant could not be updated.",
    "Thiếu DocID để publish": "Missing DocID to publish",
    "sửa metadata": "edit metadata",
    "độc lập": "standalone",
    "variant mới": "new variant",
    # --- analytics.py ---
    "Phân tích & báo cáo sử dụng": "Usage analytics & reports",
    "Thống kê câu hỏi, tài liệu được hỏi nhiều và tỉ lệ không tìm thấy — để cải thiện kho tài liệu.": "Statistics on questions, most-asked documents and not-found rate — to improve the document repository.",
    "Khoảng thời gian": "Time range",
    "ngày gần nhất": "most recent days",
    "Đang tổng hợp...": "Aggregating...",
    "Tổng câu hỏi": "Total questions",
    "Phiên / Người dùng": "Sessions / Users",
    "Tỉ lệ không tìm thấy": "Not-found rate",
    "Tỉ lệ câu trả lời cho thấy hệ thống không tìm được thông tin.": "Share of answers where the system could not find information.",
    "Số câu hỏi theo ngày": "Questions per day",
    "Chưa có dữ liệu chat trong khoảng thời gian này.": "No chat data in this time range.",
    "Câu hỏi phổ biến": "Popular questions",
    "Câu hỏi (đã chuẩn hóa)": "Question (normalized)",
    "Tài liệu được tham chiếu nhiều": "Most-referenced documents",
    "Tài liệu / bản vẽ": "Documents / drawings",
    "Chưa có tham chiếu tài liệu trong câu trả lời.": "No document references in the answers yet.",
    "Tỉ lệ 'không tìm thấy' cao ⇒ cân nhắc bổ sung tài liệu cho các chủ đề đó, hoặc kiểm tra quyền truy cập của người dùng.": "A high 'not found' rate ⇒ consider adding documents for those topics, or check users' access permissions.",
    # --- audit.py ---
    "Chỉ admin được xem audit log.": "Only admins can view the audit log.",
    "Lọc action": "Filter action",
    "Lọc username": "Filter username",
    "Khoảng ngày (CreatedAt)": "Date range (CreatedAt)",
    "Giới hạn dòng": "Row limit",
    "Không có audit log.": "No audit logs.",
    "Hiển thị {n} dòng (giới hạn {lim}).": "Showing {n} rows (limit {lim}).",
    "Tải CSV": "Download CSV",
    "Chỉ đọc tài liệu mật": "Confidential reads only",
    "Chỉ hiển thị các lượt truy cập tài liệu confidential (action read_confidential).": "Only show confidential document accesses (action read_confidential).",
    # --- documents.py ---
    "Danh sách tài liệu đã được nạp vào hệ thống.": "List of documents ingested into the system.",
    "Chọn tất cả tài liệu đang hiển thị": "Select all displayed documents",
    "Đã chọn {n} tài liệu.": "Selected {n} documents.",
    "Xác nhận xóa vĩnh viễn {n} tài liệu?": "Confirm permanently deleting {n} documents?",
    "Xóa tất cả tài liệu đã chọn": "Delete all selected documents",
    "Đánh dấu hiện hành": "Mark as current",
    "Đánh dấu hết hiệu lực": "Mark as expired",
    "Hiển các tài liệu có ExpiryDate hoặc ReviewDate trong vòng 60 ngày tới, hoặc đã quá hạn mà vẫn đang ở trạng thái active.": "Shows documents with an ExpiryDate or ReviewDate within the next 60 days, or already overdue but still active.",
    "Đã đánh dấu hiện hành.": "Marked as current.",
    "Đã cập nhật trạng thái.": "Status updated.",
    "Tiêu đề:": "Title:",
    "Đã xóa tài liệu.": "Document deleted.",
    "Xóa tài liệu thất bại.": "Failed to delete document.",
    "Đang hiệu lực": "Active",
    "Đã bị thay thế": "Superseded",
    "Đã hết hạn": "Expired",
    # --- feedback.py ---
    "Phân loại câu trả lời bị dislike để cải thiện RAG và golden set.": "Categorize disliked answers to improve RAG and the golden set.",
    "Điểm tính từ like/dislike, có trọng số theo vai trò người đánh giá và giảm dần theo thời gian; bỏ qua feedback đã stale. Điểm thấp = cần xem lại.": "Score computed from likes/dislikes, weighted by reviewer role and decaying over time; stale feedback is ignored. A low score = needs review.",
    "Tính lại điểm chất lượng": "Recalculate quality score",
    "Tập câu hỏi chuẩn + đáp án kỳ vọng (DocID và/hoặc từ khóa). Bấm Chạy hồi quy để đối chiếu câu trả lời hiện tại của bot, phát hiện hồi quy sau khi cập nhật tài liệu/cấu hình.": "A set of standard questions + expected answers (DocID and/or keywords). Click 'Run regression' to compare against the bot's current answers and detect regressions after updating documents/config.",
    "Đang có {active} câu active / {total} tổng.": "{active} active questions / {total} total.",
    "Dọn dữ liệu mồ côi (tham chiếu tới tài liệu/chat đã xoà) để điểm chất lượng và golden set không bị sai lệch.": "Clean up orphaned data (references to deleted documents/chats) so quality scores and the golden set are not skewed.",
    "Câu trả lời đúng": "Correct answer",
    "Đã tính lại cho {n} tài liệu.": "Recalculated for {n} documents.",
    "Chưa có dữ liệu. Hãy bấm nút Tính lại điểm chất lượng sau khi đã có like/dislike.": "No data yet. Click 'Recalculate quality score' after you have some likes/dislikes.",
    "Chưa có kết quả hồi quy. Thêm câu hỏi và bấm Chạy hồi quy.": "No regression results yet. Add questions and click 'Run regression'.",
    "Đang chạy bộ hồi quy qua engine RAG...": "Running the regression suite through the RAG engine...",
    "Đã dọn: ": "Cleaned: ",
    "Đã cập nhật feedback và lưu Golden Answer.": "Feedback updated and Golden Answer saved.",
    "Đã cập nhật feedback.": "Feedback updated.",
    "Đã thêm câu hỏi hồi quy.": "Regression question added.",
    "Đã xóa feedback.": "Feedback deleted.",
    # --- materials.py ---
    "Từ điển mã vật tư & đồng nghĩa": "Material code & synonym dictionary",
    "Quản trị danh mục vật liệu chuẩn + từ đồng nghĩa dùng cho trích xuất & chuẩn hóa khi ingest, và guard chống bịa vật liệu trong RAG. Chỉnh ở đây có hiệu lực ngay — không cần sửa code.": "Manage the standard material catalog + synonyms used for extraction & normalization during ingest, and to guard against fabricated materials in RAG. Changes here take effect immediately — no code changes needed.",
    "Chưa có vật liệu nào. Hãy thêm ở trên hoặc chạy migration P2 để seed dữ liệu gốc.": "No materials yet. Add one above or run the P2 migration to seed initial data.",
    "đồng nghĩa": "synonyms",
    "Thêm vật liệu mới": "Add new material",
    "Mã chuẩn (vd SUS304)": "Standard code (e.g. SUS304)",
    "Tên hiển thị (vd SUS 304)": "Display name (e.g. SUS 304)",
    "Nhóm (vd stainless steel)": "Group (e.g. stainless steel)",
    "Tổng cộng: {n} vật liệu": "Total: {n} materials",
    "Mã chuẩn": "Standard code",
    "Nhóm": "Group",
    "Lưu thay đổi": "Save changes",
    "Xóa vật liệu": "Delete material",
    "Thêm đồng nghĩa": "Add synonym",
    "Đã thêm/cập nhật '{code}'.": "Added/updated '{code}'.",
    "Phải nhập Mã chuẩn.": "Standard code is required.",
    "Đã lưu.": "Saved.",
    "Đã xóa '{code}'.": "Deleted '{code}'.",
    "Từ đồng nghĩa": "Synonyms",
    "Chưa có đồng nghĩa.": "No synonyms yet.",
    # --- users.py ---
    "Allowed sites/khu (để trống = không giới hạn)": "Allowed sites/zones (leave empty = no restriction)",
    "Mức mật tối đa": "Maximum security level",
    "Khu mặc định": "Default site",
    "Lĩnh vực / kiểu đọc (domain)": "Field / reading type (domain)",
    "Reassign / gộp phòng ban": "Reassign / merge departments",
    "Phòng nguồn": "Source department",
    "Phòng đích": "Target department",
    "Chuyển luôn user assignments sang phòng đích": "Also move user assignments to the target department",
    "Sẽ chuyển TaiLieu, IngestionJobs, UserDepartments và payload Qdrant; sau đó tự động tắt phòng nguồn.": "Will move TaiLieu, IngestionJobs, UserDepartments and the Qdrant payload; then automatically disable the source department.",
    "Thực hiện reassign": "Run reassign",
    "Bạn phải chọn đủ phòng nguồn và phòng đích.": "You must select both source and target departments.",
    "Thao tác reassign thất bại.": "Reassign operation failed.",
    "Đã tạo người dùng.": "User created.",
    "Không tạo được user: {e}": "Could not create user: {e}",
    "Đã cập nhật quyền.": "Permissions updated.",
    "Đặt lại mật khẩu": "Reset password",
    "Đã đặt lại mật khẩu.": "Password reset.",
    "Lỗi đặt lại mật khẩu: {e}": "Error resetting password: {e}",
    "User này đang còn quyền ở phòng đã đóng: {depts}": "This user still has permissions in closed departments: {depts}",
    "Đã tắt phòng {code}.": "Department {code} disabled.",
    "Đã bật phòng {code}.": "Department {code} enabled.",
    "Đã lưu phòng ban.": "Department saved.",
    "Đã lưu khu/site.": "Site/zone saved.",
    # --- chatbot.py ---
    "Hệ thống đang bận": "The system is busy",
    "Không có output từ RAG worker": "No output from the RAG worker",
    "Hôm nay": "Today",
    "Hôm qua": "Yesterday",
    "Cũ hơn": "Older",
    "**{name}**: Đã lưu và đưa vào hàng đợi xử lý ngầm (JobID: {job_id})": "**{name}**: Saved and queued for background processing (JobID: {job_id})",
    "**{name}**: Lỗi khi tạo Job": "**{name}**: Error creating job",
    "Hoàn tất đưa vào hàng đợi! (Thành công {ok}/{total})": "Finished queuing! (Success {ok}/{total})",
})

# --- Phase 7: nhan song ngu con sot (English base -> Vietnamese base) ---
_EN.update({
    "Phòng ban được phép": "Allowed departments",
    "Vai trò": "Roles",
    "Tên đăng nhập": "Username",
    "Đang hoạt động": "Active",
    "Vòng phản hồi": "Feedback Loop",
    "Nhật ký kiểm toán": "Audit Log",
})

# ---------------------------------------------------------------------------
# Build lookup: normalized source text -> EN translation
# ---------------------------------------------------------------------------

_TRANSLATIONS: dict[str, dict[str, str]] = {"en": _EN}

_NORM_EN: dict[str, str] = {_norm(k): v for k, v in _EN.items()}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_lang() -> str:
    """Tra ve lang hien tai (vi/en). Mac dinh vi."""
    return st.session_state.get(_SESSION_KEY, DEFAULT_LANG)


def set_lang(lang: str) -> None:
    """Dat lang trong session."""
    if lang in LANGUAGES:
        st.session_state[_SESSION_KEY] = lang


def t(text: str, **kwargs: Any) -> str:
    """Dich text sang ngon ngu hien tai.

    - text: chuoi tieng Viet goc.
    - kwargs: cac placeholder {key} trong chuoi.
    Tra ve chuoi da dich (EN) hoac goc (VI) voi placeholder da dien.
    """
    lang = get_lang()
    result = text
    if lang != DEFAULT_LANG:
        translations = _TRANSLATIONS.get(lang, {})
        # Thu exact match truoc
        translated = translations.get(text)
        if translated is None:
            # Thu normalized match
            translated = _NORM_EN.get(_norm(text))
        if translated is not None:
            result = translated
    if kwargs:
        try:
            result = result.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return result


def language_selector(
    label: str = "Language",
    key: str = "lang_selector",
    sidebar: bool = True,
) -> str:
    """Widget chon ngon ngu; tra ve lang hien tai."""
    container = st.sidebar if sidebar else st
    current = get_lang()
    options = list(LANGUAGES.keys())
    idx = options.index(current) if current in options else 0
    selected = container.selectbox(
        label,
        options,
        index=idx,
        format_func=lambda x: LANGUAGES[x],
        key=key,
    )
    if selected != current:
        set_lang(selected)
        st.rerun()
    return selected
