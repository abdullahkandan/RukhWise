"""One-shot generator for taxonomy_v3.yaml from taxonomy_v2.yaml, driven
exactly by output/taxonomy_v3_approved.md's rulings. Kept as a script
(not run-and-delete) so the exact mechanical steps that produced v3 are
auditable, same spirit as output/taxonomy_v3_spec.md documenting v2's
build. taxonomy_v1.yaml and taxonomy_v2.yaml are read-only inputs here,
never written.

Does NOT touch the database or any extraction -- extract.py/extract_skills.py
wiring and the actual re-extraction run happen separately, after this file
is reviewed.
"""

from __future__ import annotations

import re
from pathlib import Path

V2_PATH = Path("rukhwise_scraper/taxonomy_v2.yaml")
V3_PATH = Path("rukhwise_scraper/taxonomy_v3.yaml")

BANNER_RE = re.compile(r"^  # -{10} (\w+) \((\d+)\) -{10}$", re.MULTILINE)


def _fmt_alias(a: str) -> str:
    """Match v2's own convention: quote multi-word / special-char aliases,
    leave simple bare tokens unquoted."""
    if re.fullmatch(r"[a-z0-9_./&+-]+", a):
        return a
    return f'"{a}"'


def _fmt_entry(key: str, display: str, category: str, req_type: str, aliases: list[str], indent: str = "  ") -> str:
    alias_str = ", ".join(_fmt_alias(a) for a in aliases)
    key_str = key if re.fullmatch(r"[a-z0-9_]+", key) else f'"{key}"'
    return (
        f"{indent}{key_str}:\n"
        f"{indent}  display: {display}\n"
        f"{indent}  category: {category}\n"
        f"{indent}  requirement_type: {req_type}\n"
        f"{indent}  aliases: [{alias_str}]\n"
    )


def main() -> None:
    text = V2_PATH.read_text(encoding="utf-8")

    # ---- split into category blocks by banner comment ----
    matches = list(BANNER_RE.finditer(text))
    header = text[: matches[0].start()]
    blocks: dict[str, str] = {}
    order: list[str] = []
    for i, m in enumerate(matches):
        cat = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks[cat] = text[start:end]
        order.append(cat)

    # ---- TASK 1 collision fixes: alias additions to existing entries ----
    blocks["marketing_tech"] = blocks["marketing_tech"].replace(
        '"crm systems", "crm tools", "crm records"]',
        '"crm systems", "crm tools", "crm records", "lead-management systems"]',
    )
    blocks["language"] = blocks["language"].replace(
        '"command of english", "communication skills in english", "fluent english"]',
        '"command of english", "communication skills in english", "fluent english", "english communication skills"]',
    )

    # ---- TASK 1 collision fix: relocate customer_service (soft -> customer_service) ----
    cs_entry_re = re.compile(
        r'  customer_service:\n'
        r'    display: Customer Service\n'
        r'    category: soft\n'
        r'    requirement_type: skill\n'
        r'    aliases: \["customer service", "customer support", "customer care"\]\n'
    )
    cs_match = cs_entry_re.search(blocks["soft"])
    assert cs_match, "customer_service entry not found under soft -- v2 file shape changed?"
    blocks["soft"] = blocks["soft"].replace(cs_match.group(0), "")
    blocks["soft"] = blocks["soft"].replace("# ---------- soft (12) ----------", "# ---------- soft (11 v2 + 17 v3) ----------")

    relocated_customer_service = _fmt_entry(
        "customer_service", "Customer Service", "customer_service", "skill",
        ["customer service", "customer support", "customer care"],
    ) + "    # Relocated from v2's 'soft' category -- taxonomy_v3_approved.md section 2:\n" \
        "    # customer service is a substantive domain requirement here, not a generic trait.\n"

    # ---- TASK 2/3: new entries appended to existing categories ----
    blocks["marketing_tech"] = blocks["marketing_tech"].rstrip("\n") + "\n\n  # ---------- marketing_tech v3 additions (11) ----------\n" + "".join(
        _fmt_entry(k, d, "marketing_tech", "skill", a) + "\n" for k, d, a in [
            ("market_research", "Market Research", ["market research"]),
            ("lead_generation", "Lead Generation", ["lead generation"]),
            ("client_relationship_management", "Client Relationship Management", ["client relationship management", "client relationships"]),
            ("competitor_analysis", "Competitor Analysis", ["competitor analysis", "competitor activities"]),
            ("market_analysis", "Market Analysis", ["market analysis"]),
            ("cold_calling", "Cold Calling", ["cold calling", "cold calls"]),
            ("sales_pipeline", "Sales Pipeline", ["sales pipeline"]),
            ("business_development", "Business Development", ["business development"]),
            ("sales_strategy", "Sales Strategy", ["sales strategy development", "strategic planning"]),
            ("upwork", "Upwork", ["upwork"]),
            ("linkedin", "LinkedIn", ["linkedin"]),
        ]
    )

    blocks["analytics_bi"] = blocks["analytics_bi"].rstrip("\n") + "\n\n  # ---------- analytics_bi v3 additions (1) ----------\n" + \
        "  # microsoft_excel NOT added -- 'microsoft excel' is already an alias of the existing 'excel' entry (see collision report).\n" + \
        _fmt_entry("data_cleaning", "Data Cleaning", "analytics_bi", "skill", ["data cleaning"]) + "\n"

    blocks["data_ml"] = blocks["data_ml"].rstrip("\n") + "\n\n  # ---------- data_ml v3 additions (1) ----------\n" + \
        _fmt_entry("database_management", "Database Management", "data_ml", "skill", ["database management"]) + "\n"

    blocks["cloud_devops"] = blocks["cloud_devops"].rstrip("\n") + "\n\n  # ---------- cloud_devops v3 additions (4) ----------\n" + "".join(
        _fmt_entry(k, d, "cloud_devops", "skill", a) + "\n" for k, d, a in [
            ("vpn", "VPN", ["vpn"]),
            ("routers", "Routers", ["routers"]),
            ("switches", "Switches", ["switches"]),
            ("firewalls", "Firewalls", ["firewalls"]),
        ]
    )

    blocks["design"] = blocks["design"].rstrip("\n") + "\n\n  # ---------- design v3 additions (2) ----------\n" + "".join(
        _fmt_entry(k, d, "design", "skill", a) + "\n" for k, d, a in [
            ("technical_documentation", "Technical Documentation", ["technical documentation"]),
            ("engineering_drawings", "Engineering Drawings", ["engineering drawings", "blueprint reading"]),
        ]
    )

    blocks["soft"] = blocks["soft"].rstrip("\n") + "\n\n  # ---------- soft v3 additions (17, non-substantive per taxonomy_v3_approved.md section 3) ----------\n" + "".join(
        _fmt_entry(k, d, "soft", "skill", a) + "\n" for k, d, a in [
            ("attention_to_detail", "Attention to Detail", ["attention to detail"]),
            ("organizational_skills", "Organizational Skills", ["organizational skills"]),
            ("multitasking", "Multitasking", ["multitasking"]),
            ("conflict_resolution", "Conflict Resolution", ["conflict resolution"]),
            ("interpersonal_skills", "Interpersonal Skills", ["interpersonal skills"]),
            ("active_listening", "Active Listening", ["active listening"]),
            ("empathy", "Empathy", ["empathy"]),
            ("problem_resolution", "Problem Resolution", ["problem resolution"]),
            ("product_knowledge", "Product Knowledge", ["product knowledge"]),
            ("team_management", "Team Management", ["team management"]),
            ("team_coordination", "Team Coordination", ["team coordination"]),
            ("data_accuracy", "Data Accuracy", ["data accuracy"]),
            ("confidentiality", "Confidentiality", ["confidentiality"]),
            ("customer_satisfaction", "Customer Satisfaction", ["customer satisfaction"]),
            ("target_achievement", "Target Achievement", ["target achievement"]),
            ("community_engagement", "Community Engagement", ["community engagement"]),
            ("sales_techniques", "Sales Techniques", ["sales techniques"]),
        ]
    )

    blocks["office_admin"] = blocks["office_admin"].rstrip("\n") + "\n\n  # ---------- office_admin v3 additions (16, non-substantive) ----------\n" + \
        "  # data_entry NOT re-added -- identical key/category/alias already exists in v2 (see collision report).\n" + \
        "  # microsoft_office NOT added -- 'microsoft office' is already an alias of the existing 'ms_office' entry.\n" + "".join(
        _fmt_entry(k, d, "office_admin", "skill", a) + "\n" for k, d, a in [
            ("record_keeping", "Record Keeping", ["record keeping"]),
            ("document_management", "Document Management", ["document management"]),
            ("document_preparation", "Document Preparation", ["document preparation"]),
            ("office_administration", "Office Administration", ["office administration"]),
            ("calendar_management", "Calendar Management", ["calendar management"]),
            ("appointment_scheduling", "Appointment Scheduling", ["appointment scheduling"]),
            ("email_management", "Email Management", ["email management"]),
            ("scheduling", "Scheduling", ["scheduling"]),
            ("file_management", "File Management", ["file management"]),
            ("data_verification", "Data Verification", ["data verification"]),
            ("data_management", "Data Management", ["data management"]),
            ("basic_computer_skills", "Basic Computer Skills", ["basic computer skills"]),
            ("correspondence", "Correspondence", ["correspondence"]),
            ("documentation", "Documentation", ["documentation"]),
            ("keyboard_shortcuts", "Keyboard Shortcuts", ["keyboard shortcuts"]),
            ("travel_arrangements", "Travel Arrangements", ["travel arrangements"]),
        ]
    )

    # ---- TASK 2: 7 brand-new categories, appended after the last v2 block (work_arrangement) ----
    new_category_blocks = ""

    new_category_blocks += "  # ---------- health_clinical (4, new in v3) ----------\n" + "".join(
        _fmt_entry(k, d, "health_clinical", "skill", a) + "\n" for k, d, a in [
            ("electronic_health_records", "Electronic Health Records (EHR)", ["ehr", "electronic health records (ehr)", "electronic health records (ehr) management"]),
            ("medical_coding", "Medical Coding", ["medical coding"]),
            ("medical_billing_software", "Medical Billing Software", ["medical billing software", "medical billing"]),
            ("infection_control", "Infection Control", ["infection control"]),
        ]
    )
    new_category_blocks += "\n  # ---------- lab_science (4, new in v3) ----------\n" + "".join(
        _fmt_entry(k, d, "lab_science", "skill", a) + "\n" for k, d, a in [
            ("chemical_analysis", "Chemical Analysis", ["chemical analysis"]),
            ("sample_preparation", "Sample Preparation", ["sample preparation"]),
            ("laboratory_safety", "Laboratory Safety", ["laboratory safety", "lab safety"]),
            ("laboratory_equipment", "Laboratory Equipment Maintenance", ["laboratory equipment maintenance", "lab equipment"]),
        ]
    )
    new_category_blocks += "\n  # ---------- safety_compliance (6, new in v3) ----------\n" + "".join(
        _fmt_entry(k, d, "safety_compliance", "skill", a) + "\n" for k, d, a in [
            ("safety_compliance", "Safety Compliance", ["safety compliance", "safety standards", "safety regulations"]),
            ("risk_assessment", "Risk Assessment", ["risk assessment"]),
            ("quality_control", "Quality Control", ["quality control", "qc"]),
            ("quality_assurance", "Quality Assurance", ["quality assurance", "qa"]),
            ("regulatory_compliance", "Regulatory Compliance", ["regulatory compliance"]),
            ("ppe", "PPE", ["ppe", "personal protective equipment"]),
        ]
    )
    new_category_blocks += "\n  # ---------- supply_chain (5, new in v3) ----------\n" + "".join(
        _fmt_entry(k, d, "supply_chain", "skill", a) + "\n" for k, d, a in [
            ("inventory_management", "Inventory Management", ["inventory management", "inventory planning"]),
            ("logistics_coordination", "Logistics Coordination", ["logistics coordination"]),
            ("supplier_relationship_management", "Supplier Relationship Management", ["supplier relationship management"]),
            ("demand_planning", "Demand Planning", ["demand planning"]),
            ("supply_chain_management", "Supply Chain Management", ["supply chain management"]),
        ]
    )
    new_category_blocks += "\n  # ---------- electrical_mechanical (3, new in v3) ----------\n" + "".join(
        _fmt_entry(k, d, "electrical_mechanical", "skill", a) + "\n" for k, d, a in [
            ("preventive_maintenance", "Preventive Maintenance", ["preventive maintenance"]),
            ("electrical_systems", "Electrical Systems", ["electrical systems"]),
            ("troubleshooting", "Troubleshooting", ["troubleshooting"]),
        ]
    )
    new_category_blocks += "\n  # ---------- teaching (3, new in v3) ----------\n" + "".join(
        _fmt_entry(k, d, "teaching", "skill", a) + "\n" for k, d, a in [
            ("curriculum_development", "Curriculum Development", ["curriculum development"]),
            ("curriculum_implementation", "Curriculum Implementation", ["curriculum implementation"]),
            ("educational_technology", "Educational Technology", ["educational technology", "ed-tech"]),
        ]
    )
    new_category_blocks += "\n  # ---------- customer_service (3, new in v3 -- includes 1 entry relocated from v2's soft) ----------\n" + \
        "".join(
            _fmt_entry(k, d, "customer_service", "skill", a) + "\n" for k, d, a in [
                ("call_handling", "Call Handling", ["call handling", "phone calls handling"]),
                ("multichannel_support", "Multichannel Support", ["multichannel support"]),
            ]
        ) + relocated_customer_service

    # ---- assemble ----
    body = "".join(blocks[c] for c in order) + "\n" + new_category_blocks

    new_categories_yaml = "".join(
        f"  {c}: {{signal: normal}}\n"
        for c in ["health_clinical", "lab_science", "safety_compliance", "supply_chain", "electrical_mechanical", "teaching", "customer_service"]
    )
    header = header.replace(
        "  work_arrangement: {signal: normal}\n",
        "  work_arrangement: {signal: normal}\n" + new_categories_yaml,
    )
    header = header.replace(
        "# Rukhwise skill taxonomy v2",
        "# Rukhwise skill taxonomy v3 -- built from taxonomy_v2.yaml exactly per\n"
        "# output/taxonomy_v3_approved.md's line-by-line ruling. taxonomy_v1.yaml and\n"
        "# taxonomy_v2.yaml are left in place, untouched, as the historical record\n"
        "# extraction_method='taxonomy_v1'/'taxonomy_v2' rows were computed against.\n"
        "#\n"
        "# v3 adds 7 new categories (health_clinical, lab_science, safety_compliance,\n"
        "# supply_chain, electrical_mechanical, teaching, customer_service) plus\n"
        "# substantive/non-substantive additions to existing categories, sourced from\n"
        "# taxonomy_v3_consolidate.py's merged tag+text discovery passes and ruled on\n"
        "# by hand in taxonomy_v3_approved.md. One v2 entry (customer_service) was\n"
        "# RELOCATED from category 'soft' to the new 'customer_service' category --\n"
        "# same key, same aliases, corrected classification (see that entry's comment).\n"
        "#\n"
        "# Rukhwise skill taxonomy v2 (superseded)"
    )

    final_text = header + body
    V3_PATH.write_text(final_text, encoding="utf-8")
    print(f"Wrote {V3_PATH} ({len(final_text)} chars)")


if __name__ == "__main__":
    main()
