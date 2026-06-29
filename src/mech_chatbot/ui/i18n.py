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
