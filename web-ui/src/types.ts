export type UserProfile = {
  user_id: number;
  username: string;
  display_name?: string | null;
  department?: string | null;
  roles: string[];
  allowed_departments: string[];
  max_security_level: string;
  allowed_sites: string[];
  preferred_language?: "vi" | "en";
  csrf_token: string;
};

export type Citation = {
  doc_id: number;
  page_no: number;
  file_name?: string | null;
  score?: number | null;
  page_url: string;
  original_url: string;
};

export type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  image_url?: string | null;
  image_name?: string | null;
  chat_id?: number | null;
  feedback?: number | null;
  ref_text?: string;
  citations?: Citation[];
};

export type SessionItem = {
  session_id: string;
  thoi_gian?: string;
  cau_hoi: string;
  owner?: string;
};

export type SessionMemory = {
  currentPartIds: string[];
  conversationContext: Record<string, unknown> | null;
};
