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
  page_no: number | null;
  source_id?: string;
  citation_schema?: 'source-locator-v2';
  scope_id?: string;
  scope_hash?: string;
  snapshot_id?: string;
  source_hash?: string;
  facts_url?: string;
  locator?: { doc_id: string; version: string; page?: number | null; sheet?: string | null;
    cell?: string | null; row_key?: string; table_index?: number | null; bbox?: number[] | null };
  file_name?: string | null;
  version_no?: number | string | null;
  score?: number | null;
  has_vision: boolean;
  page_url?: string | null;
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
  advanced_rag?: AdvancedRagMetadata;
};

export type EvidenceDocument = {DocID:number; TenFile:string; Site?:string};
export type ApprovedScopeChoice = {scope_id:string; definition:string; period:string; unit:string; currency:string};
export type GraphAction = {root_key:string; product_scope:string; site:string; direction:'contains'|'where_used'};
export type BomQuantity = {order_quantity:string;quantity_scope?:never}|{quantity_scope:string;order_quantity?:never};
export type QueryAction = {branch_id:string; question:string; depends_on:string[]; graph_query?:GraphAction;
  scope_comparison?:{scope_a:string;scope_b:string}; bom_rollup?:GraphAction & BomQuantity & {rounding_scope:string}};
export type QueryActionState = {active:boolean;valid:boolean;query_plan?:{branches:QueryAction[]};text_query?:true;scope_comparison?:{scope_a:string;scope_b:string}};
export type MathEvidenceResult = {steps?:{step_id:string;formula:string;display_value?:string;value?:string|null;unit:string;status?:string}[]};
export type AdvancedRagMetadata = {text_query?:{schema:string;accepted:boolean;offline_only:boolean;
  branches:{branch_id:string;subquery:string;outcome:string;evaluator_state?:string|null;cited_sources:Citation[]}[]};
  query?:{schema:string;status:string;accepted:boolean;offline_only:boolean;
  branches:{branch_id:string;question:string;status:string;data_complete?:boolean;sources:Citation[];
    graph?:{source_status?:string};math?:MathEvidenceResult}[]}};

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
