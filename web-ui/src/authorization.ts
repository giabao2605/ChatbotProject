export type NavigationItem = {
  to: string;
  key: string;
  roles: readonly string[];
};

function normalizedRoles(roles: readonly string[] | undefined): string[] {
  return (roles ?? []).map((role) => role.toLowerCase());
}

const ROLE_CAPABILITIES: Record<string, readonly string[]> = {
  platform_admin: ["platform_admin"],
  security_admin: ["security_admin", "admin"],
  knowledge_approver: ["knowledge_approver", "reviewer", "admin"],
  reviewer: ["reviewer", "knowledge_approver", "admin"],
  uploader: ["uploader", "admin"],
  viewer: ["viewer", "knowledge_consumer", "admin"],
  knowledge_consumer: ["knowledge_consumer", "viewer", "admin"],
  admin: ["admin"],
};

/** Returns whether a user may access a route or navigation item. */
export function isRoleAllowed(
  userRoles: readonly string[] | undefined,
  requiredRoles: readonly string[],
): boolean {
  if (requiredRoles.length === 0) return true;

  const current = normalizedRoles(userRoles);
  return requiredRoles.some((role) => {
    const key = role.toLowerCase();
    const accepted = ROLE_CAPABILITIES[key] ?? [key];
    return accepted.some((candidate) => current.includes(candidate));
  });
}

/** The sidebar policy is deliberately shared with route-level authorization. */
export const NAVIGATION_ITEMS: readonly NavigationItem[] = [
  { to: "/chat", key: "nav.chat", roles: ["viewer", "uploader", "reviewer"] },
  { to: "/dashboard", key: "nav.dashboard", roles: [] },
  { to: "/documents", key: "nav.documents", roles: ["viewer", "uploader", "reviewer"] },
  { to: "/upload", key: "nav.upload", roles: ["uploader", "reviewer", "admin"] },
  { to: "/queue", key: "nav.queue", roles: ["uploader", "reviewer", "admin"] },
  { to: "/review", key: "nav.review", roles: ["reviewer", "admin"] },
  { to: "/access", key: "nav.access", roles: ["viewer", "uploader", "reviewer"] },
  { to: "/users", key: "nav.users", roles: ["security_admin"] },
  { to: "/org", key: "nav.org", roles: ["platform_admin"] },
  { to: "/dictionary", key: "nav.dictionary", roles: ["reviewer", "admin"] },
  { to: "/feedback", key: "nav.feedback", roles: ["reviewer", "admin"] },
  { to: "/regression", key: "nav.regression", roles: ["reviewer", "admin"] },
  { to: "/quality", key: "nav.quality", roles: ["reviewer", "admin"] },
  { to: "/analytics", key: "nav.analytics", roles: ["reviewer", "admin"] },
  { to: "/observability", key: "nav.observability", roles: ["platform_admin"] },
  { to: "/audit", key: "nav.audit", roles: ["platform_admin"] },
  { to: "/settings", key: "nav.settings", roles: ["platform_admin"] },
  { to: "/help", key: "nav.help", roles: [] },
];

export function visibleNavigationItems(userRoles: readonly string[] | undefined): NavigationItem[] {
  return NAVIGATION_ITEMS.filter((item) => isRoleAllowed(userRoles, item.roles));
}
