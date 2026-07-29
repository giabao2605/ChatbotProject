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

        from mech_chatbot.rag.execution import (
            AccessScope,
            DefaultRagExecutor,
            RagInvocation,
            RagRequest,
            collect_rag_events,
        )

        request = RagRequest(
            question=payload.get("user_question", ""),
            image_path=payload.get("image_path"),
            history=tuple(payload.get("chat_history") or ()),
            current_part_ids=tuple(payload.get("current_part_ids") or ()),
            access=AccessScope(
                department=payload.get("user_department"),
                roles=frozenset(payload.get("user_roles") or ()),
                allowed_departments=frozenset(payload.get("allowed_departments") or ()),
                max_security_level=payload.get("max_security_level") or "public",
                allowed_sites=frozenset(payload.get("allowed_sites") or ()),
            ),
            response_language=payload.get("response_language") or "vi",
            conversation_context=payload.get("conversation_context") or None,
        )
        result = collect_rag_events(
            DefaultRagExecutor().run(
                request,
                RagInvocation(trace_id="", mode="production"),
            )
        )

        write_output(out_path, {
            "ok": True,
            "response": result.answer,
            "ref_text": result.ref_text,
            "ref_images": list(result.ref_images),
            "new_part_ids": list(result.new_part_ids),
            "debug_info": dict(result.diagnostics),
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
