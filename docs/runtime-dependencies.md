# Dựng lại môi trường Windows đang vận hành

Phạm vi: CPython 3.12.3, Windows x64, bộ gói trong `chat_env` của checkout phục vụ
được chụp ngày 30/10/2026. Không nâng gói, cài thử hoặc sửa môi trường đang chạy.

`requirements.txt` tiếp tục khai báo dependency của dự án. `requirements.lock.txt`
cũ là snapshot lịch sử, không được thay bằng file mới một cách âm thầm.
`requirements-windows-runtime.lock.txt` ghi phiên bản và SHA256 của từng wheel để
dựng lại đúng bộ 278 distribution đang có, gồm cả công cụ kiểm thử/audit vốn đã
cài trong môi trường. Đợt này chưa tách bớt các công cụ đó để tránh đổi phạm vi.

## Nguồn và cách lưu

Wheelhouse và receipt của lần dựng nằm trong worktree, tại
`.local/runs/runtime-consolidation-20261030/dependencies/`:

- `installed.json`: danh sách phiên bản và nguồn wheel trực tiếp đã cài.
- `wheel-manifest.json`: tên, phiên bản, dung lượng và SHA256 từng wheel.
- `cached-source-origins.json`: tarball nguồn và hash của ba wheel được build từ
  source trước đó: antlr4-python3-runtime 4.9.3, langdetect 1.0.9, pylatexenc 2.10.
- `wheels/`: lưu wheel thực tế; không commit binary vào Git.
- `rebuild-verification.json`: kết quả kiểm chứng khi dựng xong.

Ba package anyio, certifi và urllib3 có nguồn cài trực tiếp từ wheel giữ lại của
lượt sửa trước; wheel mới phải khớp SHA256 trong metadata nguồn cũ. Các wheel còn
lại lấy đúng phiên bản từ PyPI/cache. Không thay phiên bản khi thiếu wheel.

Cần giữ một bộ wheelhouse đã kiểm chứng cùng hồ sơ quay lui/phát hành. Lock có
hash giúp phát hiện wheel thay đổi, nhưng một file lock không thay thế binary
đã lưu. Đặc biệt wheel build từ source có thể có hash khác nếu build lại.

## Dựng vào môi trường mới

Chạy từ repo chứa lock, thay `$wheelhouse` bằng đường dẫn bộ wheel đã lưu. Chỉ
dùng một đường dẫn venv mới, không trỏ vào `chat_env` và không hardlink/copy từ
venv đang phục vụ. Không dùng tùy chọn system-site-packages.

```powershell
$newEnv = '.local/venvs/runtime-rebuild-20261030'
$wheelhouse = '.local/runs/runtime-consolidation-20261030/dependencies/wheels'
if (Test-Path -LiteralPath $newEnv) { throw 'Chọn đường dẫn môi trường mới' }
& 'C:\Users\bao.nguyen\AppData\Local\Programs\Python\Python312\python.exe' -m venv $newEnv
if ($LASTEXITCODE -ne 0) { throw 'Không tạo được môi trường' }
& "$newEnv/Scripts/python.exe" -B -m pip install --no-index --find-links $wheelhouse --require-hashes -r requirements-windows-runtime.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dựng dependency không đạt' }
& "$newEnv/Scripts/python.exe" -B -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency không tương thích' }
```

Python host của Windows Service vẫn là interpreter Windows hiện có. Môi trường
vừa dựng chỉ kiểm chứng khả năng phục hồi dependency ứng dụng; không tự đổi host
Python hoặc thay `chat_env` production. Host được kiểm tra read-only ngày 30/10:
CPython 3.12.3 và pywin32 312, cùng phiên bản pywin32 trong lock. Giữ thông tin
này trong hồ sơ phát hành; không tự cài lại interpreter host khi dựng venv.

## Trạng thái kiểm chứng

Đã dựng lại thành công trong môi trường riêng ngày 30/10/2026, bằng cài đặt offline
`--no-index --require-hashes`, không thay phiên bản. Receipt
`rebuild-verification.json` ghi `ok=true`: đủ 278 distribution khớp snapshot,
`pip check` đạt, 56.666 file được kiểm tra không có hardlink và không có reparse
point. 11 import native/composition đạt, gồm thứ tự bcrypt, pyarrow, onnxruntime,
numpy và ba composition module App/RAG/Worker. Python socket audit hook từ chối
kết nối mạng trong lượt import; đây không phải firewall ở cấp hệ điều hành.

Wheelhouse gồm 278 wheel, tổng 593.413.879 byte. SHA256 của lock:
`e08462d5eaf3c60408b984d15562607ac96f243781197efa6838ae0e745e22bd`.
Môi trường và wheelhouse này là bộ kiểm chứng/phục hồi cần giữ cùng receipt,
không phải bản web thứ hai đang chạy. Chưa đổi Python host hoặc `chat_env` live.

Kiểm chứng yêu cầu: đủ phiên bản so với snapshot; `pip check` đạt; không có
hardlink/reparse point trong venv mới; import DLL theo thứ tự bcrypt, pyarrow,
onnxruntime, numpy và import các composition root App/RAG/Worker với mạng bị
chặn ở Python socket hook. Chỉ import `mech_chatbot.composition.*`, không import
API module có lệnh tạo FastAPI app ở cấp module. Không gọi hàm dựng runtime,
không kết nối SQL/Qdrant, không tải model
hoặc khởi động service trong bước này.

Kiểm tra dependency/import không thay thế kiểm tra identity, model, dữ liệu,
phát hành có chữ ký và các luồng web sau khi chuyển service. Không tuyên bố đã
xử lý advisory bảo mật chỉ vì giữ nguyên phiên bản và `pip check` đạt.
