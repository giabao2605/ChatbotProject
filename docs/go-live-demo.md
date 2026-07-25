# Go-Live Demo 5-10 Users

Runbook nay danh cho demo noi bo nho. Khong bat buoc Docker.

## 1. Chon kieu link

Co 3 cach phu hop:

- Noi bo LAN/VPN: chay app tren mot may trong cong ty, nguoi dung vao `http://<server-ip>:8080`.
- Link HTTPS qua tunnel: chay app tren may cua ban/server noi bo, dung Cloudflare Tunnel de cap link HTTPS.
- Ten mien that: dat app tren VM/server, dung reverse proxy nhu Caddy/Nginx tro vao app-api.

Cho demo 5-10 nguoi, nen bat dau bang LAN/VPN neu tat ca cung mang. Neu can gui link ngoai mang ma khong mo firewall, dung tunnel.

## 2. Cau hinh `.env`

Tao token noi bo cho UI -> RAG:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Dat cac bien sau trong `.env`:

```env
RAG_SERVER_HOST=127.0.0.1
RAG_SERVER_PORT=8100
RAG_SERVER_URL=http://127.0.0.1:8100
RAG_REQUIRE_SERVICE_AUTH=true
RAG_SERVICE_TOKEN=<chuoi-random-vua-tao>

MAX_CONCURRENT_RAG=4
RAG_WORKER_TIMEOUT=240

SEMANTIC_CACHE_ENABLED=true
SEMANTIC_CACHE_SIM_THRESHOLD=0.93
SEMANTIC_CACHE_TTL_HOURS=24

EMBEDDING_DEVICE=cpu
```

Neu may co GPU va Python/PyTorch da nhan CUDA:

```env
EMBEDDING_DEVICE=cuda
```

## 3. Chay migration bat buoc

Semantic cache can bang DB tu migration `V0014__semantic_cache.sql`. Truoc demo, dam bao migration da chay tren SQL Server.

Neu chua chac, kiem tra DB co bang semantic cache. Khong bat cache neu migration nay chua co.

## 4. Chay khong can Docker

Tao va kich hoat virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Neu da co `chat_env` nhu may hien tai, cach nhanh nhat la chay script:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\ops\start_demo_lan.ps1
```

Script se:

- dung process cu dang chiem `8100` va `8080`
- bat lai RAG server, app-api/Vue va worker
- kiem tra `RAG /health`
- in ra link LAN neu tu xac dinh duoc IPv4 cua may host

Neu muon chay tay, dung 2 terminal rieng:

Mo terminal 1, chay RAG server:

```powershell
$env:PYTHONPATH="src"
python -m mech_chatbot.api.rag_server
```

Mo terminal 2, chay app-api + Vue static UI:

```powershell
$env:PYTHONPATH="src"
$env:APP_SERVER_HOST="0.0.0.0"
$env:APP_SERVER_PORT="8080"
python -m mech_chatbot.api.app_server
```

Nguoi dung trong cung mang/VPN truy cap:

```text
http://<server-ip>:8080
```

Khong publish port `8100` ra ngoai. RAG server nen chi lang nghe `127.0.0.1`.

## 5. Tao link HTTPS

### Phuong an tunnel

Dung tunnel khi server nam trong mang noi bo va ban khong muon mo inbound firewall. Tunnel se tro ve app-api `http://localhost:8080`.

Chi expose UI, khong expose RAG:

```text
http://localhost:8080
```

### Phuong an ten mien that

Neu co domain va server public, dung reverse proxy tro domain ve app-api:

```text
https://chatbot.example.com -> http://127.0.0.1:8080
```

RAG van giu noi bo:

```text
http://127.0.0.1:8100
```

## 6. Smoke test truoc khi moi nguoi dung

Kiem tra nhanh:

- Dang nhap bang tai khoan viewer binh thuong.
- Hoi 1 cau co tai lieu public/internal duoc phep xem.
- Hoi 1 cau lien quan tai lieu phong ban khac de xac nhan bi chan dung.
- Hoi lai cau vua hoi lan 2 de cache co co hoi hit.
- Kiem tra trang Observability xem latency va loi.

Wave 1 (`Technical`, `HR`, `Purchasing`) vẫn là pilot và không tự động được coi
là sẵn sàng. Dùng checklist tại `docs/wave1-rollout-checklist.md`; trạng thái
`pilot` không thay thế readiness gate hoặc cho phép bỏ qua dark launch.

Wave 2 (`Warehouse`, `Accountant`, `Sales`, `Planning`) hien chi duoc chuan bi
code/configuration va phai giu trang thai `planned`. Khong dung corpus mau,
evaluation gia hoac gan quyen gia de mo dark launch. Checklist chinh thuc nam
tai `docs/wave2-rollout-checklist.md`.

Wave 3 (`Production`, `Maintenance`, `QualityControl`, `ISO`) cung chi duoc
chuan bi code/configuration va phai giu trang thai `planned`. Wave 3 khong duoc
dark launch hoac active truoc khi toan bo Wave 1 va Wave 2 active, dong thoi
chinh phong do dat readiness voi du lieu that. Checklist nam tai
`docs/wave3-rollout-checklist.md`.

Wave 4 hiện gồm `Molding`, `HSE_5S` và `IT`, được chuẩn bị ở trạng thái
`planned`; slot thứ tư được để trống cho phòng thứ 15 và không tạo placeholder.
Wave 4 chỉ được dark launch hoặc active sau khi toàn bộ Wave 1--3 active và
chính phòng đó đạt readiness bằng dữ liệu thật. Checklist nằm tại
`docs/wave4-rollout-checklist.md`.

## 7. Gioi han demo

Voi `MAX_CONCURRENT_RAG=4`, demo nen moi 5-10 nguoi dung va nhac moi nguoi khong spam cau hoi lien tuc. Neu CPU gan 100%, RAM tang manh, hoac nhieu loi busy/timeout, giam toc demo hoac ha `MAX_CONCURRENT_RAG=2`.

## 8. Docker van la tuy chon

Neu muon chay Docker Compose:

```powershell
docker compose -f docker/docker-compose.yml up -d --build
```

Kiem tra trang thai:

```powershell
docker compose -f docker/docker-compose.yml ps
docker compose -f docker/docker-compose.yml logs -f rag-server
```

Trong Docker Compose, RAG port `8100` chi bind `127.0.0.1`. Nguoi dung demo chi truy cap app-api/Vue qua port `8080`.

## 9. Rollback nhanh

Neu cache gay loi do DB migration thieu:

```env
SEMANTIC_CACHE_ENABLED=false
```

Neu server qua tai:

```env
MAX_CONCURRENT_RAG=2
```

Restart lai cac process RAG/app-api/worker sau khi doi `.env`.
