import { NextRequest } from "next/server";
import { verifyContextToken } from "@/lib/bridge";
import { ragPost, userPayload } from "@/lib/rag";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function parseCtx(token: string | null) {
  if (!token) throw new Error("Missing ctx");
  return verifyContextToken(token);
}

export async function GET(req: NextRequest) {
  try {
    const ctx = parseCtx(req.nextUrl.searchParams.get("ctx"));
    const data = await ragPost("/chat/sessions", userPayload(ctx));
    return Response.json(data);
  } catch (e) {
    return new Response((e as Error).message, { status: 401 });
  }
}

export async function DELETE(req: NextRequest) {
  try {
    const body = (await req.json()) as { ctx?: string; session_id?: string };
    const ctx = parseCtx(body.ctx ?? null);
    if (!body.session_id) {
      return new Response("Missing session_id", { status: 400 });
    }
    const data = await ragPost("/chat/history/delete", {
      ...userPayload(ctx),
      session_id: body.session_id,
    });
    return Response.json(data);
  } catch (e) {
    return new Response((e as Error).message, { status: 401 });
  }
}
