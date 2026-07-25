/**
 * Human labels for taxonomy category keys. Must cover every category in
 * the ACTIVE taxonomy (api/queries.py's ACTIVE_TAXONOMY, currently
 * taxonomy_v3.yaml) -- a category missing here isn't just mislabeled, it's
 * unselectable: SkillGapPicker's CATEGORY_ORDER is derived from these keys,
 * so a skill whose category is absent from this map never appears in the
 * picker at all. This previously happened to all 7 v3-only categories
 * (health_clinical..customer_service) AND to v2's language/work_arrangement
 * -- fixed by listing every category the taxonomy defines, not just the
 * original v1 set.
 */
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
  language: "Language",
  work_arrangement: "Work Arrangement",
  health_clinical: "Health & Clinical",
  lab_science: "Lab & Science",
  safety_compliance: "Safety & Compliance",
  supply_chain: "Supply Chain",
  electrical_mechanical: "Electrical & Mechanical",
  teaching: "Teaching",
  customer_service: "Customer Service",
};

export function categoryLabel(key: string): string {
  return CATEGORY_LABELS[key] ?? key;
}
