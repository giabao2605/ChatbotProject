import crypto from "crypto";

export type ChatContext = {
  user_id?: number | null;
  username?: string | null;
  user_department?: string | null;
  user_roles: string[];
  allowed_departments: string[];
  max_security_level: string;
  allowed_sites: string[];
  response_language: string;
  exp?: number;
};

function b64urlDecode(input: string): Buffer {
  const pad = input.length % 4 === 0 ? "" : "=".repeat(4 - (input.length % 4));
  const normalized = input.replace(/-/g, "+").replace(/_/g, "/") + pad;
  return Buffer.from(normalized, "base64");
}

/**
 * Xac thuc token ngu canh do Streamlit tao ra (HMAC-SHA256 voi CHAT_BRIDGE_SECRET).
 * Token co dang "<body>.<signature>", trong do body la JSON base64url.
 */
export function verifyContextToken(token: string): ChatContext {
  const secret = process.env.CHAT_BRIDGE_SECRET;
  if (!secret) throw new Error("CHAT_BRIDGE_SECRET is not configured");

  const parts = token.split(".");
  if (parts.length !== 2) throw new Error("Malformed token");
  const [body, sig] = parts;

  const expected = crypto.createHmac("sha256", secret).update(body).digest();
  const actual = b64urlDecode(sig);
  if (
    expected.length !== actual.length ||
    !crypto.timingSafeEqual(expected, actual)
  ) {
    throw new Error("Invalid signature");
  }

  const payload = JSON.parse(
    b64urlDecode(body).toString("utf8"),
  ) as ChatContext;

  if (payload.exp && Date.now() / 1000 > payload.exp) {
    throw new Error("Token expired");
  }
  return payload;
}
