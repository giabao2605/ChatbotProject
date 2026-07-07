import { NextRequest } from "next/server";
import { verifyContextToken } from "@/lib/bridge";
import { ragPost, userPayload } from "@/lib/rag";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as {
      ctx?: string;
      chat_id?: number;
      rating?: number;
    };
    if (!body.ctx) return new Response("Missing ctx", { status: 401 });
    if (!body.chat_id || !body.rating) {
      return new Response("Missing feedback data", { status: 400 });
    }
    const ctx = verifyContextToken(body.ctx);
    const data = await ragPost("/chat/feedback", {
      ...userPayload(ctx),
      chat_id: body.chat_id,
      rating: body.rating,
    });
    return Response.json(data);
  } catch (e) {
    return new Response((e as Error).message, { status: 401 });
  }
}
