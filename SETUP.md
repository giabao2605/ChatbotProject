# Huong dan ghep giao dien chat Next.js vao Mech Chatbot

Muc tieu: chi doi giao dien phan CHAT (nhung vao tab "Chatbot hoi dap" cua Streamlit).
Cac trang khac (Kho tai lieu, Nguoi dung, ...) giu nguyen Streamlit cu.
Cau tra loi chay chu dan (streaming), va cac may cung LAN deu thay giao dien moi.

Goi giao hang gom 4 phan, copy vao dung vi tri trong du an cua ban:

```
chat-ui/                                  -> copy nguyen thu muc vao goc du an
src/mech_chatbot/ui/pages/chat_bridge.py  -> file MOI
scripts/ops/start_demo_lan.ps1            -> GHI DE file cu (da cap nhat)
```
Va 1 chinh sua nho trong `src/mech_chatbot/ui/pages/chatbot.py` (xem Buoc 3).

---

## Buoc 1 - Them bien bi mat vao file .env chinh

Mo file `.env` o goc du an, them 1 dong (chuoi bi mat dai, ngau nhien):

```
CHAT_BRIDGE_SECRET=<chuoi_bi_mat_dai>
```

Tao nhanh mot chuoi ngau nhien:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Dam bao `.env` da co san (ban da xac nhan):
- `RAG_SERVICE_TOKEN=...`  (token dich vu, chuoi dai - da co)
- `RAG_SERVER_URL=http://127.0.0.1:8100`  (RAG chay cong 8100 - da co)

> Luu y: `CHAT_BRIDGE_SECRET` chi dung de Streamlit va app Next.js "tin" nhau ve
> thong tin dang nhap. No KHAC voi `RAG_SERVICE_TOKEN`.

---

## Buoc 2 - Cai va build app chat Next.js

Can co Node.js (>= 18) tren may host.

```bash
cd chat-ui
npm install
npm run build
```

(Chi can lam 1 lan. Sau nay doi IP LAN cung khong can build lai.)

---

## Buoc 3 - Bat che do nhung trong chatbot.py

Mo `src/mech_chatbot/ui/pages/chatbot.py`, tim dong:

```python
def run_chat():
```

Them NGAY duoi dong do (dau ham) doan sau:

```python
    # --- Nhung giao dien chat Next.js (bat bang USE_NEXTJS_CHAT trong moi truong) ---
    if os.getenv("USE_NEXTJS_CHAT", "").strip().lower() in ("1", "true", "yes", "on"):
        from mech_chatbot.ui.pages.chat_bridge import render_nextjs_chat
        render_nextjs_chat()
        return
```

(`os` da duoc import san o dau file chatbot.py nen khong can them import.)

De TAT (quay lai chat Streamlit cu): chi can bo bien `USE_NEXTJS_CHAT` (hoac dat = 0).
Doan code tren se khong chay, moi thu tro lai nhu cu.

---

## Buoc 4 - Chay demo LAN

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ops\start_demo_lan.ps1
```

Script se:
1. Tat tien trinh cu o cong 8100 / 8501 / 3000.
2. Bat RAG server (8100).
3. Bat app chat Next.js (3000, mo ra LAN).
4. Bat Streamlit (8501, mo ra LAN) voi USE_NEXTJS_CHAT=1 va CHAT_UI_BASE_URL tro
   dung IP LAN cua may host (tranh bay localhost).
5. In ra dong: `==> LINK DEMO (mo tren cac may cung LAN): http://<IP>:8501`

Gui link do cho cac may khac trong mang.

---

## Ket qua khi dung

- Mo link `http://<IP-may-ban>:8501` -> hien man login cu -> dang nhap admin.
- Menu ben trai giu nguyen.
- Bam "Chatbot hoi dap"  -> GIAO DIEN CHAT MOI (Next.js), tra loi chay chu dan.
- Bam "Kho tai lieu" / cac trang khac -> van la Streamlit cu.
- Cac may khac trong LAN vao cung link :8501 cung thay y het.

---

## Ky thuat / bao mat (tom tat)

- Browser KHONG bao gio thay `RAG_SERVICE_TOKEN`. Trang chat goi `/api/chat` cua
  chinh Next.js; server Next.js moi dinh token roi goi RAG.
- Streamlit truyen thong tin phan quyen (user_id, roles, department,
  allowed_departments, max_security_level, allowed_sites) qua mot token co ky HMAC.
  App Next.js xac thuc bang CHAT_BRIDGE_SECRET truoc khi goi RAG -> nguoi dung khong
  the tu sua quyen tu trinh duyet.
- Streaming hien tai la kieu "go chu" tu cau tra loi day du cua RAG (khong phai token
  that tu LLM), nen KHONG can sua RAG server. Neu sau nay muon token that tu LLM,
  can them mot endpoint SSE trong rag_server.py va goi thay cho /chat.
- ref_images (anh can cu) la duong dan file cuc bo tren may host nen chua hien
  truc tiep tren giao dien moi; phan "Nguon tham khao" (ref_text) van hien.
