# Mech Chat UI (Next.js)

Giao dien chat moi cho Mech Chatbot, thay cho phan chat cua Streamlit. Duoc nhung
vao tab "Chatbot hoi dap" cua Streamlit qua iframe, va goi thang toi RAG server
(FastAPI) san co qua mot lop proxy phia server (giau service token).

## Cai dat (chi lam 1 lan)

```bash
cd chat-ui
npm install
npm run build
```

## Chay

Cach 1 - qua script demo LAN (khuyen nghi): xem `scripts/ops/start_demo_lan.ps1`.
Script se tu doc bien tu `.env` chinh va truyen vao, roi mo cong 3000 ra LAN.

Cach 2 - chay tay de phat trien:

```bash
cp .env.local.example .env.local   # dien RAG_SERVER_URL, RAG_SERVICE_TOKEN, CHAT_BRIDGE_SECRET
npm run dev
```

## Bien moi truong

| Bien | Y nghia |
|------|---------|
| `RAG_SERVER_URL` | Dia chi RAG server, vd `http://127.0.0.1:8100` |
| `RAG_SERVICE_TOKEN` | Token dich vu goi RAG (giu phia server, khong lo ra browser) |
| `CHAT_BRIDGE_SECRET` | Bi mat de xac thuc token ngu canh tu Streamlit. PHAI trung voi `.env` chinh |

## Luong hoat dong

1. Streamlit (tab Chatbot) tao mot token co ky HMAC chua thong tin phan quyen cua
   nguoi dang dang nhap, roi nhung iframe `http://<IP-LAN>:3000/?ctx=<token>`.
2. Trang chat goi `POST /api/chat` (cung origin).
3. `/api/chat` xac thuc token, dinh kem `X-RAG-Service-Token`, goi `POST /chat`
   cua RAG server, roi phat cau tra loi ve trinh duyet TUNG TU MOT (hieu ung go chu).

Browser khong bao gio nhin thay `RAG_SERVICE_TOKEN`.
