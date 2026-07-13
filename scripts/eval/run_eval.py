import json
import os
import sys
import time
from collections import defaultdict

os.environ.setdefault("RAG_EXECUTION_CONTEXT", "evaluation")

# Them thu muc goc vao sys.path de import duoc rag_logic
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mech_chatbot.rag.service import chat_with_rag, extract_search_intent
from mech_chatbot.config.logging import logger
from mech_chatbot.evaluation.outcomes import REFUSAL_OUTCOMES, expected_outcome, summarize_outcomes

def run_evaluation():
    golden_set_file = os.path.join(os.path.dirname(__file__), "golden_set_datagoc_real.jsonl")
    output_file = os.path.join(os.path.dirname(__file__), "eval_report.md")
    
    if not os.path.exists(golden_set_file):
        print(f"Khong tim thay file {golden_set_file}")
        return

    test_cases = []
    with open(golden_set_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                test_cases.append(json.loads(line))

    print("BAT DAU CHAY EVALUATION RAG... (vui long doi, ket qua se duoc luu vao file eval_report.md)")

    total_tests = len(test_cases)
    passed_tests = 0
    
    # Metrics
    metrics = {
        "keyword_pass": 0,
        "source_pass": 0,
        "forbidden_violation": 0,
        "refusal_pass": 0,
        "policy_pass": 0,
        "total_latency": 0.0
    }
    
    level_stats = defaultdict(lambda: {"total": 0, "pass": 0})
    outcome_rows = []

    with open(output_file, "w", encoding="utf-8") as out:
        out.write("#  Kết Quả Đánh Giá RAG Pipeline (Phase 4)\n\n")
        out.write(f"**Tổng số câu hỏi test:** {total_tests}\n\n")
        out.write("---\n\n")
        
        for i, case in enumerate(test_cases):
            test_id = case.get("id", f"Test_{i+1}")
            level = case.get("level", "N/A")
            question = case.get("question", "")
            expected_keywords = case.get("expected_keywords", [])
            expected_sources = case.get("expected_sources", [])
            forbidden_sources = case.get("forbidden_sources", [])
            expected_version_policy = case.get("expected_version_policy", "current_only")
            expected_case_outcome = expected_outcome(case)
            should_refuse = expected_case_outcome in REFUSAL_OUTCOMES
            user_department = case.get("user_department")
            user_roles = case.get("user_roles")
            allowed_departments = case.get("allowed_departments")
            
            level_stats[level]["total"] += 1
            
            print(f"Dang chay cau {i+1}/{total_tests}: [{test_id}]...")
            
            out.write(f"### Câu {i+1}: [{test_id}] {level}\n")
            out.write(f"** Câu hỏi:** {question}\n\n")
            out.write(f"- **Kỳ vọng Keywords:** {expected_keywords}\n")
            if expected_sources: out.write(f"- **Kỳ vọng Sources:** {expected_sources}\n")
            if forbidden_sources: out.write(f"- **Forbidden Sources:** {forbidden_sources}\n")
            out.write(f"- **Kỳ vọng Version Policy:** {expected_version_policy}\n")
            out.write(f"- **Từ chối (Should refuse):** {should_refuse}\n\n")
            
            start_time = time.time()
            
            try:
                # 1. Evaluate Intent Extraction
                try:
                    strict_f, broad_f, p_ids, is_inh, is_bom, intent_data = extract_search_intent(question, [], user_department, user_roles, allowed_departments)
                    actual_policy = intent_data.get("version_policy", "current_only") if intent_data else "current_only"
                except Exception as e:
                    # Backward compatibility if intent extraction format changes
                    strict_f, broad_f, p_ids, is_inh, is_bom = extract_search_intent(question, [], user_department, user_roles, allowed_departments)
                    actual_policy = "current_only"
                    
                policy_passed = (actual_policy == expected_version_policy)
                if policy_passed: metrics["policy_pass"] += 1
                
                # 2. Goi RAG
                stream, ref_text, ref_images, new_part_ids, debug_info = chat_with_rag(question, None, [], [], user_department, user_roles, allowed_departments)
                bot_answer = ""
                for chunk in stream:
                    bot_answer += chunk
                
                latency = time.time() - start_time
                metrics["total_latency"] += latency
                
                bot_answer_lower = bot_answer.lower()
                ref_text_lower = (ref_text or "").lower()
                
                # Check keywords
                keywords_passed = True
                failed_keywords = []
                if should_refuse:
                    if expected_keywords:
                        keywords_passed = any(kw.lower() in bot_answer_lower for kw in expected_keywords)
                        if not keywords_passed:
                            failed_keywords = expected_keywords
                else:
                    for kw in expected_keywords:
                        if kw.lower() not in bot_answer_lower:
                            keywords_passed = False
                            failed_keywords.append(kw)
                if keywords_passed: metrics["keyword_pass"] += 1
                
                # Check refusal
                refusal_keywords = ["không ghi thông tin", "tài liệu hiện tại không", "từ chối", "không đủ", "thiếu dữ kiện", "không tự ước lượng"]
                actual_refused = any(rk in bot_answer_lower for rk in refusal_keywords)
                access_denied = any(
                    marker in bot_answer_lower
                    for marker in ["chưa đủ quyền", "access request", "protected by access control"]
                )
                actual_case_outcome = (
                    "access_denied" if actual_refused and access_denied
                    else "insufficient_evidence" if actual_refused
                    else "full_answer"
                )
                refusal_passed = (should_refuse == actual_refused)
                if refusal_passed: metrics["refusal_pass"] += 1
                
                # Check expected sources (Recall@5 approx)
                sources_passed = True
                failed_sources = []
                retrieved_files = [
                    str(d.get("file_goc", "")).lower() 
                    for d in debug_info.get("retrieved_docs", [])[:5]
                ]
                for src in expected_sources:
                    # Check in debug_info or in bot_answer text
                    found = False
                    for rf in retrieved_files:
                        if src.lower() in rf:
                            found = True
                            break
                    if not found and src.lower() not in bot_answer_lower:
                        sources_passed = False
                        failed_sources.append(src)
                if sources_passed: metrics["source_pass"] += 1
                
                # Check forbidden sources
                forbidden_passed = True
                violated_sources = []
                for src in forbidden_sources:
                    found = False
                    for rf in retrieved_files:
                        if src.lower() in rf:
                            found = True
                            break
                    if found or src.lower() in bot_answer_lower:
                        forbidden_passed = False
                        violated_sources.append(src)
                if not forbidden_passed: metrics["forbidden_violation"] += 1
                
                # Final result for the test
                is_pass = keywords_passed and refusal_passed and sources_passed and forbidden_passed and policy_passed
                outcome_rows.append(
                    {
                        "expected": expected_case_outcome,
                        "actual": actual_case_outcome,
                        "answer_correct": is_pass,
                        "leaked": not forbidden_passed,
                    }
                )
                
                if is_pass:
                    passed_tests += 1
                    level_stats[level]["pass"] += 1
                    status_icon = " **PASSED**"
                else:
                    status_icon = " **FAILED**"
                
                out.write(f"**Trạng thái:** {status_icon}\n\n")
                if not is_pass:
                    if not policy_passed:
                        out.write(f"- Lỗi: Policy sai. Ky vong: `{expected_version_policy}`, Thuc te: `{actual_policy}`\n")
                    if not keywords_passed:
                        out.write(f"- Lỗi: Không khớp keywords kỳ vọng: {failed_keywords}\n")
                    if not refusal_passed:
                        out.write(f"- Lỗi: Phản hồi từ chối không khớp kỳ vọng (Kỳ vọng từ chối: {should_refuse}, Thực tế: {actual_refused})\n")
                    if not sources_passed:
                        out.write(f"- Lỗi: Không tìm thấy nguồn: {failed_sources}\n")
                    if not forbidden_passed:
                        out.write(f"- Lỗi: Vi pham Forbidden Source: {violated_sources}\n")
                    out.write("\n")

                out.write(f"** Thời gian:** {latency:.2f}s\n\n")
                out.write(f"** Bot trả lời:**\n> {bot_answer.strip().replace(chr(10), chr(10)+'> ')}\n\n")
                
                if ref_text:
                    out.write(f"** Nguồn trích dẫn (Bot):**\n{ref_text.strip()}\n\n")
                
                out.write("---\n")

            except Exception as e:
                out.write(f"**Trạng thái:**  ERROR\n\n")
                out.write(f"**Lỗi RAG:** {e}\n\n---\n")

        # Summary 
        out.write(f"\n## TỔNG KẾT METRICS\n")
        out.write(f"- **Tổng số câu test:** {total_tests}\n")
        out.write(f"- **Pass toàn phần:** {passed_tests} ({(passed_tests/total_tests)*100 if total_tests > 0 else 0:.1f}%)\n")
        out.write(f"- **Answer Keyword Score:** {metrics['keyword_pass']}/{total_tests} ({(metrics['keyword_pass']/total_tests)*100 if total_tests > 0 else 0:.1f}%)\n")
        out.write(f"- **Retrieval Recall@5:** {metrics['source_pass']}/{total_tests} ({(metrics['source_pass']/total_tests)*100 if total_tests > 0 else 0:.1f}%)\n")
        out.write(f"- **Forbidden Violation:** {metrics['forbidden_violation']}/{total_tests}\n")
        out.write(f"- **Refusal Score:** {metrics['refusal_pass']}/{total_tests} ({(metrics['refusal_pass']/total_tests)*100 if total_tests > 0 else 0:.1f}%)\n")
        out.write(f"- **Version Policy Score:** {metrics['policy_pass']}/{total_tests} ({(metrics['policy_pass']/total_tests)*100 if total_tests > 0 else 0:.1f}%)\n")
        avg_lat = metrics['total_latency']/total_tests if total_tests > 0 else 0
        out.write(f"- **Average Latency:** {avg_lat:.2f}s/query\n\n")
        out.write("## OUTCOME CONFUSION\n")
        for name, count in summarize_outcomes(outcome_rows).items():
            out.write(f"- **{name}:** {count}\n")
        out.write("\n")
        
        out.write(f"## KẾT QUẢ THEO NHÓM (LEVEL)\n")
        
        THRESHOLDS = {
            "L1": 90,
            "L2": 85,
            "L3": 95,
            "L4": 98,
            "L5": 90,
            "L6": 90,
        }
        
        overall_failed_threshold = False
        
        for lvl, st in level_stats.items():
            rate = (st["pass"]/st["total"])*100 if st["total"] > 0 else 0
            
            # Match threshold based on the first two characters (e.g., L1_keyword -> L1)
            base_lvl = lvl.split('_')[0] if '_' in lvl else lvl
            threshold = THRESHOLDS.get(base_lvl)
            
            if threshold is not None:
                passed_threshold = rate >= threshold
                if not passed_threshold:
                    overall_failed_threshold = True

                out.write(
                    f"- **{lvl}**: Pass {st['pass']}/{st['total']} "
                    f"({rate:.1f}%) | Threshold: {threshold}% | "
                    f"{'PASS' if passed_threshold else 'FAIL'}\n"
                )
            else:
                out.write(f"- **{lvl}**: Pass {st['pass']}/{st['total']} ({rate:.1f}%)\n")

    print(f"\nDa hoan tat test. Pass {passed_tests}/{total_tests}. Vui long mo file scripts/eval_report.md de xem ket qua chi tiet.")
    
    if overall_failed_threshold:
        print("\n CI/CD ALERT: Có ít nhất 1 level không đạt ngưỡng threshold yêu cầu.")
        import sys
        sys.exit(1)

if __name__ == "__main__":
    # Ep kieu in stdout mac dinh thanh utf-8 cho chac an tren windows
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    run_evaluation()
