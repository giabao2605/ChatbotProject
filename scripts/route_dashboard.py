# -*- coding: utf-8 -*-
"""Dashboard telemetry cho Interaction Router [P2].

Doc log JSONL (logs/rag_trace.jsonl) va tong hop:
  - Phan bo route (chitchat / capability / ... / technical_query)
  - Tang xu ly (L0_rule / L1_semantic / L2_llm / default / L-1_safety)
  - Ti le FALLBACK LLM (L2_llm) va default  -> Acceptance P2: LLM fallback <= ~15-20%
  - So ca safety_block + tu choi (refusal)

Cach chay (tu goc repo):
    python scripts/route_dashboard.py                 # doc logs/rag_trace.jsonl
    python scripts/route_dashboard.py --log <path>    # chi ro file log
    python scripts/route_dashboard.py --since 2026-07-01   # loc theo ngay (ISO, so sanh chuoi)
"""
import argparse
import json
import os
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_LOG = os.path.join(os.path.dirname(_HERE), "logs", "rag_trace.jsonl")

# Cac tang do interaction_router.classify() sinh ra (loai tru event legacy L2_llm_intent).
_ROUTER_LAYERS = {"L-1_safety", "L0_rule", "L1_semantic", "L2_llm", "default"}


def _bar(frac, width=28):
    n = int(round(frac * width))
    return "#" * n + "." * (width - n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default=_DEFAULT_LOG)
    ap.add_argument("--since", default=None, help="Chi tinh event co ts >= gia tri nay (ISO)")
    args = ap.parse_args()

    if not os.path.isfile(args.log):
        print("[!] Khong tim thay log: %s" % args.log)
        print("    Chay he thong de sinh log, hoac chi ro --log <path>.")
        return

    routes = Counter()
    layers = Counter()
    total = 0
    safety = 0
    safety_reasons = Counter()
    refusals = 0
    rag_end = 0
    legacy_intent_chitchat = 0

    with open(args.log, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if args.since and str(ev.get("ts", "")) < args.since:
                continue
            name = ev.get("event")
            if name == "route":
                layer = ev.get("layer")
                if layer in _ROUTER_LAYERS:
                    total += 1
                    routes[ev.get("route", "?")] += 1
                    layers[layer] += 1
                elif layer == "L2_llm_intent":
                    legacy_intent_chitchat += 1
            elif name == "safety":
                safety += 1
                safety_reasons[ev.get("reason", "?")] += 1
            elif name == "rag_end":
                rag_end += 1
                if ev.get("refusal"):
                    refusals += 1

    print("=" * 52)
    print(" INTERACTION ROUTER DASHBOARD")
    print(" log: %s" % args.log)
    if args.since:
        print(" since: %s" % args.since)
    print("=" * 52)

    if total == 0:
        print("Chua co event 'route' nao (tu router) trong log.")
        return

    print("\nTong so lan dinh tuyen: %d\n" % total)
    print("-- Phan bo ROUTE --")
    for r, c in routes.most_common():
        frac = c / total
        print("  %-16s %5d  %5.1f%%  %s" % (r, c, frac * 100, _bar(frac)))

    print("\n-- Phan bo TANG (layer) --")
    for l, c in layers.most_common():
        frac = c / total
        print("  %-16s %5d  %5.1f%%  %s" % (l, c, frac * 100, _bar(frac)))

    llm_fb = layers.get("L2_llm", 0)
    default_fb = layers.get("default", 0)
    llm_pct = llm_fb / total * 100
    fast_pct = (layers.get("L0_rule", 0) + layers.get("L1_semantic", 0)) / total * 100

    print("\n-- CHI SO ACCEPTANCE P2 --")
    print("  Xu ly nhanh L0+L1 (khong LLM): %.1f%%" % fast_pct)
    print("  Fallback LLM (L2_llm):         %.1f%%  [muc tieu <= 15-20%%]  %s" % (
        llm_pct, "OK" if llm_pct <= 20.0 else "CAO - can them prototype/nguong"))
    print("  Fallback default (technical):  %.1f%%" % (default_fb / total * 100))
    print("  Safety_block:                  %d ca  %s" % (
        safety, dict(safety_reasons) if safety else ""))
    if rag_end:
        print("  Tu choi (refusal) / rag_end:   %d / %d (%.1f%%)" % (
            refusals, rag_end, refusals / rag_end * 100))
    if legacy_intent_chitchat:
        print("  (legacy L2_llm_intent chitchat: %d - khong tinh vao router stats)" % legacy_intent_chitchat)


if __name__ == "__main__":
    main()
