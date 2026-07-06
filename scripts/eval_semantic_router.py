# -*- coding: utf-8 -*-
"""Do do chinh xac THAT cua Interaction Router (L1 - Semantic Router) tren embedding model that.

Router CHI can EMBEDDING MODEL, KHONG can Qdrant (Qdrant chi dung cho retrieval).

Cach chay (tu thu muc goc repo, trong venv co langchain_huggingface):

    # Do accuracy + latency o nguong hien tai (mac dinh 0.62 / 0.04):
    python scripts/eval_semantic_router.py

    # Chi ro nguong:
    python scripts/eval_semantic_router.py --threshold 0.55 --margin 0.05

    # Do NGUONG TOI UU (grid search) tren bo cau vang:
    python scripts/eval_semantic_router.py --sweep

    # Dung bo cau vang rieng:
    python scripts/eval_semantic_router.py --golden duong_dan/cua_ban.csv

    # Tu kiem tra script (dung embedder gia dinh, khong can model):
    python scripts/eval_semantic_router.py --selftest --sweep

Bien moi truong dung khi build embedder (khop service.py):
    EMBEDDING_MODEL (mac dinh BAAI/bge-m3), EMBED_DEVICE (mac dinh cpu)
"""
import argparse
import csv
import hashlib
import math
import os
import sys
import time

# Cho phep chay truc tiep tu goc repo (layout src/)
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(os.path.dirname(_HERE), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from mech_chatbot.rag import chitchat
from mech_chatbot.rag import route_config
from mech_chatbot.rag import interaction_router as router

ROUTES = [
    router.ROUTE_CHITCHAT,
    router.ROUTE_CAPABILITY,
    router.ROUTE_HOW_TO_USE,
    router.ROUTE_OUT_OF_SCOPE,
    router.ROUTE_TECHNICAL,
]


def build_real_embedder():
    """Khoi tao embedder GIONG HET service.py de khop vector da index."""
    from langchain_huggingface import HuggingFaceEmbeddings
    model = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
    device = os.getenv("EMBED_DEVICE", "cpu")
    print("[embedder] loading HuggingFaceEmbeddings model=%s device=%s ..." % (model, device))
    emb = HuggingFaceEmbeddings(
        model_name=model,
        model_kwargs={"device": device},
        encode_kwargs={"normalize_embeddings": True},
    )
    return lambda t: emb.embed_query(t)


def build_fake_embedder():
    """Embedder gia dinh (bag-of-tokens, bo dau) - chi de tu kiem tra script."""
    def fe(text):
        toks = chitchat.normalize(text).split()
        if not toks:
            return None
        dim = 512
        v = [0.0] * dim
        for t in toks:
            v[int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16) % dim] += 1.0
        n = math.sqrt(sum(x * x for x in v))
        return [x / n for x in v] if n > 0 else v
    return fe


def load_golden(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            text = (r.get("text") or "").strip()
            exp = (r.get("expected_route") or "").strip()
            if text and exp:
                rows.append((text, exp))
    return rows


def predict(is_l0, scores, thr, margin):
    """Tai hien logic classify(): L0 rule -> L1 semantic (thr+margin) -> fallback technical."""
    if is_l0:
        return router.ROUTE_CHITCHAT, router.LAYER_RULE
    if scores:
        top_r, top_s = scores[0]
        second = scores[1][1] if len(scores) > 1 else 0.0
        if top_s >= thr and (top_s - second) >= margin:
            return top_r, router.LAYER_SEMANTIC
    return router.ROUTE_TECHNICAL, router.LAYER_DEFAULT


def accuracy_for(cache, thr, margin):
    correct = 0
    per = {r: [0, 0] for r in ROUTES}  # [correct, total]
    for _t, exp, is_l0, scores in cache:
        pred, _ = predict(is_l0, scores, thr, margin)
        per[exp][1] += 1
        if pred == exp:
            correct += 1
            per[exp][0] += 1
    overall = correct / len(cache) if cache else 0.0
    macro = sum((c / t if t else 0.0) for c, t in per.values()) / len(per)
    return overall, macro, per


def p_quantile(xs, q):
    if not xs:
        return 0.0
    s = sorted(xs)
    i = min(len(s) - 1, int(math.ceil(q * len(s)) - 1))
    return s[max(0, i)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=os.path.join(os.path.dirname(_HERE), "eval", "golden_routes.csv"))
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--margin", type=float, default=None)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--errors", action="store_true", help="In cac cau bi phan loai sai")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    thr = args.threshold if args.threshold is not None else route_config.semantic_threshold()
    margin = args.margin if args.margin is not None else route_config.semantic_margin()

    rows = load_golden(args.golden)
    if args.limit:
        rows = rows[: args.limit]
    print("[data] %d cau vang tu %s" % (len(rows), args.golden))

    embedder = build_fake_embedder() if args.selftest else build_real_embedder()
    sr = router.SemanticRouter(embedder)

    # Warmup (khong tinh gio) + build prototype embeddings
    embedder("warmup")
    sr.route_scores("warmup")

    # Embed 1 lan/cau, cache diem + do latency L1 (embed cua chinh cau hoi)
    cache = []  # (text, expected, is_l0, scores)
    l1_latencies = []
    for text, exp in rows:
        is_l0 = chitchat.is_chitchat(text)
        t0 = time.perf_counter()
        scores = sr.route_scores(text)  # bao gom embed_query(text) + cosine
        dt = (time.perf_counter() - t0) * 1000.0
        if not is_l0:
            l1_latencies.append(dt)
        cache.append((text, exp, is_l0, scores))

    n_l0 = sum(1 for _t, _e, is_l0, _s in cache if is_l0)
    print("[info] L0 (rule) bat: %d | L1-eligible: %d" % (n_l0, len(cache) - n_l0))

    overall, macro, per = accuracy_for(cache, thr, margin)
    print("\n==== KET QUA @ threshold=%.3f margin=%.3f ====" % (thr, margin))
    print("Accuracy tong: %.1f%%  | Macro-avg (theo route): %.1f%%" % (overall * 100, macro * 100))
    print("%-16s %8s %8s" % ("route", "recall", "n"))
    for r in ROUTES:
        c, t = per[r]
        print("%-16s %7.1f%% %8d" % (r, (c / t * 100 if t else 0.0), t))

    # Confusion matrix
    print("\n---- Confusion (hang=that, cot=du doan) ----")
    idx = {r: i for i, r in enumerate(ROUTES)}
    short = [r[:8] for r in ROUTES]
    m = [[0] * len(ROUTES) for _ in ROUTES]
    for _t, exp, is_l0, scores in cache:
        pred, _ = predict(is_l0, scores, thr, margin)
        m[idx[exp]][idx.get(pred, idx[router.ROUTE_TECHNICAL])] += 1
    print("%-16s" % "that\\pred" + "".join("%9s" % s for s in short))
    for r in ROUTES:
        print("%-16s" % r[:16] + "".join("%9d" % m[idx[r]][j] for j in range(len(ROUTES))))

    if args.errors:
        print("\n---- CAC CAU BI PHAN LOAI SAI @ %.3f/%.3f ----" % (thr, margin))
        nshow = 0
        for text, exp, is_l0, scores in cache:
            pred, _layer = predict(is_l0, scores, thr, margin)
            if pred != exp:
                nshow += 1
                top = ", ".join("%s=%.2f" % (r, s) for r, s in scores[:3])
                print("  [that=%-14s doan=%-14s] %s\n      top: %s" % (exp, pred, text, top))
        if nshow == 0:
            print("  (khong co cau nao sai)")

    # Latency
    if l1_latencies:
        print("\n---- Latency L1 (embed_query + cosine), ms ----")
        print("p50=%.1f  p95=%.1f  max=%.1f  (n=%d)" % (
            p_quantile(l1_latencies, 0.5), p_quantile(l1_latencies, 0.95),
            max(l1_latencies), len(l1_latencies)))
        print("Luu y: p95<50ms thuong can GPU hoac model nho; tren CPU bge-m3 se cham hon.")

    # Sweep
    if args.sweep:
        print("\n==== DO NGUONG TOI UU (grid search) ====")
        grid_thr = [round(x, 2) for x in [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.62, 0.65, 0.70, 0.75, 0.80]]
        grid_mgn = [0.0, 0.02, 0.04, 0.06, 0.08, 0.10]
        results = []
        for th in grid_thr:
            for mg in grid_mgn:
                ov, mac, _ = accuracy_for(cache, th, mg)
                results.append((mac, ov, th, mg))
        # Uu tien macro -> overall -> nguong CAO hon (an toan, it dinh tuyen nham) -> margin cao
        results.sort(reverse=True)
        best = results[0]
        print("Top 8 cau hinh (uu tien macro-avg):")
        print("%8s %8s %10s %8s" % ("macro", "overall", "threshold", "margin"))
        for mac, ov, th, mg in results[:8]:
            print("%7.1f%% %7.1f%% %10.2f %8.2f" % (mac * 100, ov * 100, th, mg))
        print("\n>>> DE XUAT: SEMANTIC_ROUTER_SIM_THRESHOLD=%.2f  SEMANTIC_ROUTER_MARGIN=%.2f" % (best[2], best[3]))
        print("    (macro-avg %.1f%%, overall %.1f%%)" % (best[0] * 100, best[1] * 100))


if __name__ == "__main__":
    main()
