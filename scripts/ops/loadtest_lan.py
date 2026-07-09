#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
loadtest_lan.py -- Mo phong NHIEU may client tren LAN tu MOT may duy nhat, de do
"tran" xu ly (concurrency ceiling) cua Mech Chatbot ma KHONG can mo thu cong tung may.

Y tuong:
  1. Login 1 lan -> lay cookie phien (mech_app_session) + csrf_token.
  2. Ban song song N request /api/chat/message (moi request = 1 "may ao").
  3. Do do tre, ty le thanh cong, so request bi tu choi "he thong dang ban" (503).
  4. (Tuy chon) Poll RAG /health de xem so slot con trong (current_available) theo thoi gian thuc.

Chi dung thu vien chuan cua Python (khong can cai them). Nen chay bang python cua chat_env:
  chat_env\\Scripts\\python.exe scripts\\ops\\loadtest_lan.py --help

Vi du:
  python scripts/ops/loadtest_lan.py \\
      --base-url http://192.168.1.50:8080 \\
      --username demo --password demo123 \\
      --concurrency 10 --requests 30 \\
      --question "Quy trinh bao tri may bom la gi?" \\
      --health-url http://192.168.1.50:8100/health
"""
import argparse
import http.cookiejar
import json
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def login(base_url: str, username: str, password: str, timeout: int = 30):
    """Login va tra ve (cookie_header, csrf_token)."""
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/auth/login", data=body, method="POST"
    )
    req.add_header("Content-Type", "application/json")
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise SystemExit(
            "Login that bai HTTP %s: %s" % (exc.code, exc.read()[:300])
        )
    data = json.loads(raw)
    csrf = (data.get("user") or {}).get("csrf_token")
    if not csrf:
        raise SystemExit("Login OK nhung khong thay csrf_token trong phan hoi.")
    cookie_header = "; ".join("%s=%s" % (c.name, c.value) for c in jar)
    if not cookie_header:
        raise SystemExit("Login OK nhung khong nhan duoc cookie phien.")
    return cookie_header, csrf


def send_chat(base_url, cookie_header, csrf, session_id, question, timeout):
    """Gui 1 request chat, doc het stream. Tra ve dict ket qua."""
    payload = {
        "session_id": session_id,
        "question": question,
        "chat_history": [],
        "current_part_ids": [],
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/chat/message", data=body, method="POST"
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("X-CSRF-Token", csrf)
    req.add_header("Cookie", cookie_header)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()  # doc het SSE stream -> giu slot RAG toi khi tra loi xong
            dt = time.perf_counter() - t0
            text = raw.decode("utf-8", errors="ignore")
            busy = ('"event": "error"' in text or "RAG server busy" in text
                    or "dang ban" in text or "\\u0111ang b\\u1eadn" in text)
            return {"ok": not busy, "status": resp.status, "busy": busy,
                    "latency": dt, "bytes": len(raw)}
    except urllib.error.HTTPError as exc:
        dt = time.perf_counter() - t0
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore")[:200]
        except Exception:
            pass
        busy = exc.code in (429, 503)
        return {"ok": False, "status": exc.code, "busy": busy,
                "latency": dt, "error": detail}
    except Exception as exc:  # timeout, connection reset...
        dt = time.perf_counter() - t0
        return {"ok": False, "status": None, "busy": False,
                "latency": dt, "error": repr(exc)}


def pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    k = int(round((p / 100.0) * (len(s) - 1)))
    return s[k]


def main():
    ap = argparse.ArgumentParser(description="Load test LAN cho Mech Chatbot")
    ap.add_argument("--base-url", required=True, help="VD: http://192.168.1.50:8080")
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--concurrency", type=int, default=10,
                    help="So request chay song song (so 'may ao' cung luc)")
    ap.add_argument("--requests", type=int, default=30,
                    help="Tong so request se ban ra")
    ap.add_argument("--question", default="Xin chao, ban co the giup gi?")
    ap.add_argument("--timeout", type=int, default=310,
                    help="Timeout moi request (giay)")
    ap.add_argument("--session-prefix", default="loadtest")
    ap.add_argument("--health-url", default=None,
                    help="VD: http://192.168.1.50:8100/health (tuy chon)")
    args = ap.parse_args()

    print("[*] Dang login...")
    cookie_header, csrf = login(args.base_url, args.username, args.password)
    print("[+] Login OK. Bat dau ban tai:")
    print("    - Dong thoi (concurrency): %d" % args.concurrency)
    print("    - Tong request          : %d" % args.requests)
    print("    - Muc tieu              : %s/api/chat/message" % args.base_url.rstrip("/"))

    # --- Poll /health nen (tuy chon) de xem slot con trong theo thoi gian thuc ---
    stop = threading.Event()
    health_samples = []

    def poll_health():
        while not stop.is_set():
            try:
                with urllib.request.urlopen(args.health_url, timeout=5) as r:
                    d = json.loads(r.read())
                    health_samples.append(
                        (d.get("current_available"), d.get("max_concurrent"),
                         d.get("rag_loaded"))
                    )
            except Exception:
                pass
            stop.wait(0.5)

    hp = None
    if args.health_url:
        hp = threading.Thread(target=poll_health, daemon=True)
        hp.start()

    # --- Ban tai ---
    results = []
    wall0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        futs = [
            ex.submit(
                send_chat, args.base_url, cookie_header, csrf,
                "%s-%d" % (args.session_prefix, i), args.question, args.timeout,
            )
            for i in range(args.requests)
        ]
        done = 0
        for f in as_completed(futs):
            results.append(f.result())
            done += 1
            print("\r    Hoan tat %d/%d" % (done, args.requests), end="", flush=True)
    wall = time.perf_counter() - wall0
    print()

    stop.set()
    if hp:
        hp.join(timeout=2)

    # --- Tong ket ---
    lat = [r["latency"] for r in results]
    ok = sum(1 for r in results if r["ok"])
    busy = sum(1 for r in results if r.get("busy"))
    err = sum(1 for r in results if not r["ok"] and not r.get("busy"))

    print("\n================ KET QUA ================")
    print("Tong thoi gian     : %.1fs" % wall)
    print("Throughput         : %.2f req/s (hoan tat)" % (len(results) / wall if wall else 0))
    print("Thanh cong         : %d" % ok)
    print("Bi tu choi (ban)   : %d  <-- cham TRAN MAX_CONCURRENT_RAG / timeout hang doi" % busy)
    print("Loi khac           : %d" % err)
    print("-- Do tre (giay) --")
    print("  min / trung binh : %.2f / %.2f" % (min(lat) if lat else 0,
                                                statistics.mean(lat) if lat else 0))
    print("  p50 / p95 / p99  : %.2f / %.2f / %.2f" % (pct(lat, 50), pct(lat, 95), pct(lat, 99)))
    print("  max              : %.2f" % (max(lat) if lat else 0))

    if health_samples:
        avails = [s[0] for s in health_samples if isinstance(s[0], int)]
        maxc = next((s[1] for s in health_samples if s[1] is not None), "?")
        print("-- Quan sat /health --")
        print("  max_concurrent (tran cau hinh): %s" % maxc)
        if avails:
            print("  slot con trong thap nhat       : %s  (0 = da bao hoa hoan toan)" % min(avails))
        print("  so lan poll                     : %d" % len(health_samples))

    # Goi y doc ket qua
    print("\n================ DIEN GIAI ================")
    if busy > 0:
        print("* Da cham TRAN: co %d request bi tu choi. Tang MAX_CONCURRENT_RAG (env)" % busy)
        print("  hoac giam tai neu muon phuc vu nhieu nguoi cung luc hon.")
    else:
        print("* Chua cham tran o muc concurrency=%d. Tang dan --concurrency de tim diem gay." % args.concurrency)
    print("* Meo: chay lai voi --concurrency 2,4,8,16,32 de ve duong cong do tre theo tai.")


if __name__ == "__main__":
    main()
