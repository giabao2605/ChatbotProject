// Lightweight reactive i18n. Avoids an external runtime dependency so the
// existing offline build/toolchain keeps working while still giving Vi/En
// parity synced with the user's preferred_language.
import { reactive } from "vue";
import { messages, type Locale } from "@/i18n/messages";

const STORAGE_KEY = "mech_locale";

function readStored(): Locale | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw === "en" || raw === "vi" ? raw : null;
  } catch {
    return null;
  }
}

export const i18nState = reactive<{ locale: Locale }>({ locale: "vi" });

export function setLocale(locale: Locale): void {
  i18nState.locale = locale === "en" ? "en" : "vi";
  try {
    localStorage.setItem(STORAGE_KEY, i18nState.locale);
  } catch {
    /* ignore storage errors */
  }
  if (typeof document !== "undefined") {
    document.documentElement.setAttribute("lang", i18nState.locale);
  }
}

// Resolve the initial locale from (in order) the user's preference, local
// storage, then the default.
export function initLocale(preferred?: string | null): void {
  const initial = (preferred === "en" || preferred === "vi" ? preferred : null) ?? readStored() ?? "vi";
  setLocale(initial);
}

export function currentLocale(): Locale {
  return i18nState.locale;
}

export function t(key: string, params?: Record<string, string | number>): string {
  const dict = messages[i18nState.locale] ?? messages.vi;
  let text = dict[key] ?? messages.vi[key] ?? key;
  if (params) {
    for (const [name, value] of Object.entries(params)) {
      text = text.replace(new RegExp(`\\{${name}\\}`, "g"), String(value));
    }
  }
  return text;
}

export function useI18n() {
  return { t, setLocale, currentLocale, state: i18nState };
}

export type { Locale };
