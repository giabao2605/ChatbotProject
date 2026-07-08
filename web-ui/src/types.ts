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

// ---------------------------------------------------------------------------
// Operations views (P5). Backend list endpoints return either dict rows (named
// columns) or positional arrays; ResourcePage normalizes both, so views treat
// rows as generic records.
// ---------------------------------------------------------------------------
export type ApiRow = Record<string, unknown>;

export type OkResult = { ok: boolean; result?: unknown };

export type ColumnKind = "text" | "tag" | "date" | "code" | "bool" | "score" | "link";

export type ResourceColumn = {
  field: string;
  header: string;
  kind?: ColumnKind;
  width?: string;
};

export type RowAction = {
  label: string;
  severity?: string;
  outlined?: boolean;
  confirm?: string;
  visible?: (row: ApiRow) => boolean;
  run: (row: ApiRow) => Promise<unknown>;
};

export type ToolbarAction = {
  label: string;
  severity?: string;
  outlined?: boolean;
  confirm?: string;
  run: () => Promise<unknown>;
};

export type FormFieldType = "text" | "number" | "checkbox" | "textarea" | "select";

export type FormField = {
  key: string;
  label: string;
  type?: FormFieldType;
  options?: Array<{ label: string; value: string | number | boolean }>;
  required?: boolean;
  placeholder?: string;
  help?: string;
};

export type CreateForm = {
  title?: string;
  triggerLabel?: string;
  fields: FormField[];
  submit: (values: Record<string, unknown>) => Promise<unknown>;
};

export type ResourceFilter = {
  key: string;
  label: string;
  type?: "text" | "select" | "checkbox";
  options?: Array<{ label: string; value: string | number | boolean }>;
  value?: string | number | boolean;
};
