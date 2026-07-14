/** Human labels for taxonomy category keys. */
export const CATEGORY_LABELS: Record<string, string> = {
  programming: "Programming",
  analytics_bi: "Analytics & BI",
  data_ml: "Data & ML",
  marketing_tech: "Marketing Tech",
  web_ecommerce: "Web & Ecommerce",
  enterprise_erp: "Enterprise & ERP",
  cloud_devops: "Cloud & DevOps",
  design: "Design",
  accounting_finance: "Accounting & Finance",
  office_admin: "Office & Admin",
  soft: "Soft Skills",
};

export function categoryLabel(key: string): string {
  return CATEGORY_LABELS[key] ?? key;
}
