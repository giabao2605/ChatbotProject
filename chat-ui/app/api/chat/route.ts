import { NextRequest } from "next/server";
import { verifyContextToken } from "@/lib/bridge";
import { ragBaseUrl, ragHeaders, ragPost, userPayload } from "@/lib/rag";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type ClientBody = {
  ctx?: string;
  session_id?: string;
  question?: string;
  image_path?: string | null;
  chat_history?: Array<{ role: string; content: string }>;
  current_part_ids?: string[];
  conversation_context?: Record<string, unknown> | null;
};

type RagResponse = {
  ok?: boolean;
  response?: string;
  ref_text?: string;
  ref_images?: string[];
  new_part_ids?: string[];
  debug_info?: Record<string, unknown>;
};

function isEnglish(lang?: string | null) {
  return String(lang || "").toLowerCase().startsWith("en");
}

function tr(lang: string | undefined | null, vi: string, en: string) {
  return isEnglish(lang) ? en : vi;
}

export async function POST(req: NextRequest) {
  let body: ClientBody;
  try {
    body = (await req.json()) as ClientBody;
  } catch {
    return new Response("Bad request", { status: 400 });
  }

  if (!body.ctx) return new Response("Thiếu ctx", { status: 401 });

  let ctx;
  try {
    ctx = verifyContextToken(body.ctx);
  } catch (e) {
    return new Response("Unauthorized: " + (e as Error).message, {
      status: 401,
    });
  }

  const lang = ctx.response_language ?? "vi";
  const question = (body.question ?? "").trim();
  if (!question) return new Response(tr(lang, "Câu hỏi trống", "Empty question"), { status: 400 });
  const sessionId = (body.session_id ?? "").trim();
  if (!sessionId) return new Response(tr(lang, "Thiếu session_id", "Missing session_id"), { status: 400 });

  const ragPayload = {
    user_id: ctx.user_id ?? null,
    username: ctx.username ?? null,
    user_question: question,
    image_path: body.image_path ?? null,
    chat_history: body.chat_history ?? [],
    current_part_ids: body.current_part_ids ?? [],
    user_department: ctx.user_department ?? null,
    user_roles: ctx.user_roles ?? [],
    allowed_departments: ctx.allowed_departments ?? [],
    max_security_level: ctx.max_security_level ?? "internal",
    allowed_sites: ctx.allowed_sites ?? [],
    response_language: lang,
    conversation_context: body.conversation_context ?? null,
  };

  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      const send = (event: string, data: unknown) => {
        controller.enqueue(
          encoder.encode(
            `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`,
          ),
        );
      };
      try {
        const resp = await fetch(`${ragBaseUrl()}/chat`, {
          method: "POST",
          headers: ragHeaders(),
          body: JSON.stringify(ragPayload),
        });

        if (!resp.ok) {
          const detail = await resp.text().catch(() => "");
          send("error", {
            message: `RAG server error (HTTP ${resp.status})`,
            detail,
          });
          return;
        }

        const data = (await resp.json()) as RagResponse;
        const answer = data.response ?? "";
        const refText = data.ref_text ?? "";
        const debug = data.debug_info ?? {};

        let chatId: number | null = null;
        try {
          const saved = await ragPost<{ chat_id?: number | null }>(
            "/chat/history/save",
            {
              ...userPayload(ctx),
              session_id: sessionId,
              user_msg: question,
              bot_msg: answer + refText,
              image_path: body.image_path ?? null,
              ref_images: data.ref_images ?? [],
              retrieved_docs: Array.isArray(debug["retrieved_docs"])
                ? (debug["retrieved_docs"] as unknown[])
                : [],
            },
          );
          chatId = saved.chat_id ?? null;
        } catch (saveError) {
          send("warning", {
            message: tr(
              lang,
              "Không lưu được lịch sử chat vào SQL Server",
              "Could not save chat history to SQL Server",
            ),
            detail: (saveError as Error).message,
          });
        }

        // Phat tung token de tao hieu ung go chu dan.
        const tokens = answer.match(/\S+\s*|\s+/g) ?? [answer];
        for (const tok of tokens) {
          send("delta", { text: tok });
          await new Promise((r) => setTimeout(r, 16));
        }

        send("done", {
          chat_id: chatId,
          ref_text: refText,
          ref_images: data.ref_images ?? [],
          new_part_ids: data.new_part_ids ?? [],
          conversation_context: debug["conversation_context"] ?? null,
        });
      } catch (e) {
        send("error", { message: (e as Error).message });
      } finally {
        controller.close();
      }
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      "X-Accel-Buffering": "no",
    },
  });
}
