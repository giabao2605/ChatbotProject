import { NextRequest } from "next/server";
import { verifyContextToken } from "@/lib/bridge";
import { ragPost, userPayload } from "@/lib/rag";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json()) as { ctx?: string; session_id?: string };
    if (!body.ctx) return new Response("Missing ctx", { status: 401 });
    if (!body.session_id) {
      return new Response("Missing session_id", { status: 400 });
    }
    const ctx = verifyContextToken(body.ctx);
    const data = await ragPost("/chat/history", {
      ...userPayload(ctx),
      session_id: body.session_id,
    });
    return Response.json(data);
  } catch (e) {
    return new Response((e as Error).message, { status: 401 });
  }
}
