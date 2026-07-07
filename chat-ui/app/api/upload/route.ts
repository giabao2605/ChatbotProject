import { randomUUID } from "crypto";
import { mkdir, writeFile } from "fs/promises";
import path from "path";
import { NextRequest } from "next/server";
import { verifyContextToken } from "@/lib/bridge";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const allowedExtensions = new Set([
  ".png",
  ".jpg",
  ".jpeg",
  ".bmp",
  ".gif",
  ".webp",
  ".tif",
  ".tiff",
]);
const maxBytes = 15 * 1024 * 1024;

function projectRoot() {
  return path.basename(process.cwd()).toLowerCase() === "chat-ui"
    ? path.resolve(process.cwd(), "..")
    : process.cwd();
}

function safeExtension(name: string) {
  const ext = path.extname(name || "").toLowerCase();
  return allowedExtensions.has(ext) ? ext : "";
}

export async function POST(req: NextRequest) {
  try {
    const form = await req.formData();
    const ctxToken = String(form.get("ctx") || "");
    if (!ctxToken) return new Response("Missing ctx", { status: 401 });
    verifyContextToken(ctxToken);

    const file = form.get("file");
    if (!(file instanceof File)) {
      return new Response("Missing file", { status: 400 });
    }

    const ext = safeExtension(file.name);
    if (!ext) {
      return new Response("Only image files are supported in chat", {
        status: 400,
      });
    }
    if (file.size > maxBytes) {
      return new Response("File is too large", { status: 400 });
    }

    const bytes = Buffer.from(await file.arrayBuffer());
    const dir = path.join(projectRoot(), "data", "raw", "Chat_Images");
    await mkdir(dir, { recursive: true });

    const savedPath = path.join(dir, `${randomUUID().replace(/-/g, "")}${ext}`);
    await writeFile(savedPath, bytes);

    return Response.json({
      ok: true,
      image_path: savedPath,
      file_name: file.name,
    });
  } catch (e) {
    return new Response((e as Error).message, { status: 500 });
  }
}
