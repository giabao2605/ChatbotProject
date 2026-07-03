import json
import os
import sys
import traceback


def write_output(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def main():
    from mech_chatbot.config.validate import assert_config_valid
    assert_config_valid()
    if len(sys.argv) != 3:
        raise SystemExit("Usage: rag_worker.py input.json output.json")

    in_path, out_path = sys.argv[1], sys.argv[2]

    try:
        with open(in_path, "r", encoding="utf-8") as f:
            payload = json.load(f)

        from mech_chatbot.rag.service import chat_with_rag

        rag_result = chat_with_rag(
            user_question=payload.get("user_question", ""),
            image_path=payload.get("image_path"),
            chat_history=payload.get("chat_history") or [],
            current_part_ids=payload.get("current_part_ids") or [],
            user_department=payload.get("user_department"),
            user_roles=payload.get("user_roles") or [],
            allowed_departments=payload.get("allowed_departments") or [],
            max_security_level=payload.get("max_security_level") or "public",
            allowed_sites=payload.get("allowed_sites") or [],
            response_language=payload.get("response_language") or "vi",
            conversation_context=payload.get("conversation_context") or None,
        )

        if len(rag_result) >= 4:
            stream = rag_result[0]
            ref_text = rag_result[1]
            ref_images = rag_result[2]
            new_part_ids = rag_result[3]
            debug_info = rag_result[4] if len(rag_result) >= 5 else {}
        else:
            raise ValueError(f"chat_with_rag trả về thiếu dữ liệu: {len(rag_result)} values")

        chunks = []
        for chunk in stream:
            chunks.append(str(chunk))

        write_output(out_path, {
            "ok": True,
            "response": "".join(chunks),
            "ref_text": ref_text or "",
            "ref_images": ref_images or [],
            "new_part_ids": new_part_ids or [],
            "debug_info": debug_info,
        })

        # Avoid native-library teardown crashes (onnxruntime/torch/tokenizers/etc.)
        os._exit(0)

    except Exception as e:
        try:
            write_output(out_path, {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(),
            })
        finally:
            os._exit(1)


if __name__ == "__main__":
    main()
