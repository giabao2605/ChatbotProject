import json
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from rag_logic import chat_with_rag

def collect_stream(stream):
    chunks = []
    for c in stream:
        chunks.append(str(c))
    return "".join(chunks)

def run_eval():
    eval_file = os.path.join("tests", "golden_questions.json")
    if not os.path.exists(eval_file):
        print(f"File {eval_file} not found.")
        return

    with open(eval_file, "r", encoding="utf-8") as f:
        cases = json.load(f)
        
    passed = 0
    failed = 0
    
    for case in cases:
        question = case["question"]
        print(f"Testing: {question}")
        
        result = chat_with_rag(
            user_question=question,
            chat_history=[],
            current_part_ids=[],
            user_department=None,
            user_roles=["admin"],
            allowed_departments=[]
        )
        
        answer = collect_stream(result[0])
        ref_text = result[1] or ""
        
        ok = True
        reasons = []
        
        for s in case.get("expected_answer_contains", []):
            if s.lower() not in answer.lower():
                ok = False
                reasons.append(f"Missing expected answer text: {s}")
                
        expected_file = case.get("expected_source_file")
        if expected_file and expected_file.lower() not in ref_text.lower() and expected_file.lower() not in answer.lower():
            ok = False
            reasons.append(f"Missing expected source file: {expected_file}")
            
        for s in case.get("must_not_contain", []):
            if s.lower() in answer.lower():
                ok = False
                reasons.append(f"Contains forbidden phrase: {s}")
                
        if ok:
            passed += 1
            print(f"[PASS] {question}")
        else:
            failed += 1
            print(f"[FAIL] {question}")
            print("Answer:", answer)
            print("Reasons:", reasons)
        print("-" * 80)
        
    print(f"Passed: {passed}, Failed: {failed}")

if __name__ == "__main__":
    run_eval()
