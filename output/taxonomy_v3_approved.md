# Taxonomy v3 Approved List

Ruling on all 155 candidates from the 2026-07-24 consolidated sheet. Categories reassigned against an expanded vocabulary. Nothing here is applied until Abdullah confirms.

## Structural finding: the two passes are not equal

Tag pass produces canonical employer-declared skill names. Text pass produces bare nouns lifted from sentences (`sales`, `calls`, `email`, `records`, `chat`, `prospects`, `fixtures`, `breakers`).

**Rule applied throughout:** a text-pass candidate is accepted only if it is multi-word and specific, or a proper-noun technology/platform. Single generic nouns are rejected regardless of company count. Tag-pass candidates are accepted at face value unless genuinely soft.

**Permanent method change:** tags first, text only for sources without tags (Indeed, LinkedIn), and text candidates always filtered by the rule above.

---

## 1. New categories (7)

| Category | Definition | Anchored by |
|---|---|---|
| `health_clinical` | Clinical practice, medical records, coding, patient safety | EHR, medical coding, medical billing software, infection control |
| `lab_science` | Laboratory technique, analysis, lab operations | chemical analysis, sample preparation, laboratory safety, laboratory equipment maintenance |
| `safety_compliance` | Workplace safety, regulatory adherence, QA/QC, risk | PPE, safety compliance, risk assessment, regulatory compliance, quality control |
| `supply_chain` | Inventory, procurement, logistics, supplier management | inventory management, logistics coordination, supplier relationship management, demand planning |
| `electrical_mechanical` | Electrical, mechanical, equipment maintenance and repair | preventive maintenance, electrical systems, troubleshooting |
| `teaching` | Curriculum, instruction, educational delivery | curriculum development, curriculum implementation, educational technology |
| `customer_service` | Customer contact handling and service delivery | call handling, multichannel support, customer service |

All seven are anchored to tag-pass evidence spanning multiple companies. Total categories after v3: 11 (v1) + language + work_arrangement (v2) + these 7 = 20.

---

## 2. Approved: substantive entries

### health_clinical
| Entry | Aliases | Evidence |
|---|---|---|
| electronic_health_records | EHR, Electronic Health Records (EHR), Electronic Health Records (EHR) Management | 2p/2c, **merge group #1 approved** |
| medical_coding | medical coding | 2p/2c |
| medical_billing_software | medical billing software, medical billing | 2p/2c |
| infection_control | infection control | 2p/2c |

### lab_science
| Entry | Aliases | Evidence |
|---|---|---|
| chemical_analysis | chemical analysis | 2p/2c |
| sample_preparation | sample preparation | 2p/2c |
| laboratory_safety | laboratory safety, lab safety | 2p/2c |
| laboratory_equipment | laboratory equipment maintenance, lab equipment | 2p/2c |

### safety_compliance
| Entry | Aliases | Evidence |
|---|---|---|
| safety_compliance | safety compliance, safety standards, safety regulations | 4p/4c (+2p from text, merged) |
| risk_assessment | risk assessment | 2p/2c |
| quality_control | quality control, QC | 2p/2c |
| quality_assurance | quality assurance, QA | 2p/2c |
| regulatory_compliance | regulatory compliance | 3p/2c |
| ppe | PPE, personal protective equipment | 2p/2c |

Note: `Safety Compliance` and `Quality Control` were categorised `soft` by the model. Both are substantive domain requirements, corrected here.

### supply_chain
| Entry | Aliases | Evidence |
|---|---|---|
| inventory_management | inventory management, inventory planning | 7p/5c healthcare, 6p/6c logistics, 2p/2c trades. **Strongest cross-domain candidate in the sheet.** |
| logistics_coordination | logistics coordination | 5p/5c |
| supplier_relationship_management | supplier relationship management | 3p/3c |
| demand_planning | demand planning | 2p/2c |
| supply_chain_management | supply chain management | 2p/2c |

`inventory planning` folded into inventory_management as alias.

### electrical_mechanical
| Entry | Aliases | Evidence |
|---|---|---|
| preventive_maintenance | preventive maintenance | 3p/3c, **both passes** |
| electrical_systems | electrical systems | 2p/2c |
| troubleshooting | troubleshooting | 3p/3c |

`Troubleshooting` was assigned `cloud_devops`; it appeared in trades postings about physical equipment. Reassigned.

### teaching
| Entry | Aliases | Evidence |
|---|---|---|
| curriculum_development | curriculum development | 4p/3c |
| curriculum_implementation | curriculum implementation | 2p/2c |
| educational_technology | educational technology, ed-tech | 2p/2c |

Both curriculum entries were marked `soft, substantive=no`. Corrected, as flagged. Kept separate: development and implementation are distinct competencies.

### customer_service
| Entry | Aliases | Evidence |
|---|---|---|
| call_handling | call handling, phone calls handling | 2p/2c |
| multichannel_support | multichannel support | 3p/2c |
| customer_service | customer service | 5p/5c sales, 2p/2c bpo |

### Additions to existing categories

**marketing_tech**
| Entry | Aliases | Evidence |
|---|---|---|
| market_research | market research | 14p/14c, **both passes, strongest single candidate** |
| crm | CRM, CRM systems, lead-management systems | 10p/10c |
| lead_generation | lead generation | 5p/5c + 2p/2c |
| client_relationship_management | client relationship management, client relationships | 4p/4c + 4p/4c merged |
| competitor_analysis | competitor analysis, competitor activities | 3p/3c + 4p/3c merged |
| market_analysis | market analysis | 3p/3c |
| cold_calling | cold calling, cold calls | 4p/4c + 3p/3c merged |
| sales_pipeline | sales pipeline | 3p/3c |
| business_development | business development | 4p/4c |
| sales_strategy | sales strategy development, strategic planning | 3p/3c + 4p/4c merged |
| upwork | Upwork | 4p/4c, freelance platform, relevant to PK market |
| linkedin | LinkedIn | 4p/4c, as a platform skill not a source |

**analytics_bi**: `data_cleaning` (6p/6c), `microsoft_excel` (3p/3c, check v2 for existing Excel entry first)

**data_ml**: `database_management` (3p/3c admin + 3p/3c tech)

**cloud_devops**: `vpn`, `routers`, `switches`, `firewalls` (2-3p each). Check v2 first, networking may already be covered. These are the only bare-noun text candidates accepted, because they are unambiguous networking technologies.

**design**: `technical_documentation` (3p/3c), `engineering_drawings` (2p/2c + 2p/2c, alias: blueprint reading)

**language**: `english communication skills` → alias of existing `english` entry, not a new entry

---

## 3. Approved: soft and office_admin (non-substantive)

These are accepted as taxonomy entries but do NOT count toward skill_substantive. Filing them correctly is the point.

**soft**: attention_to_detail (37p/30c, the single most common tag in the corpus), organizational_skills, multitasking, conflict_resolution, interpersonal_skills, active_listening, empathy, problem_resolution, product_knowledge, team_management, team_coordination, data_accuracy, confidentiality, customer_satisfaction, target_achievement, community_engagement, sales_techniques

**office_admin**: record_keeping, document_management, document_preparation, office_administration, calendar_management, appointment_scheduling, email_management, scheduling, file_management, data_entry, data_verification, data_management, basic_computer_skills, microsoft_office, correspondence, documentation, keyboard_shortcuts, travel_arrangements

Note: `Data Management` and `Data Verification` were assigned `analytics_bi`. In admin postings these mean record upkeep and clerical checking, not analysis. Filed office_admin.

---

## 4. Rejected, with reasons

**Bare nouns from text pass (reject all):** sales, calls, email, phone, chat, records, reports, contracts, proposals, presentations, quotations, follow-ups, prospects, referrals, sales activities, sales reports, sales targets, market trends, permissions, office supplies, consultations, progress reports, conceptual understanding, KPIs, complaints

These are sentence fragments, not requirements. Accepting them would repeat the n-gram failure.

**Trades fabrication vocabulary (reject for now):** conduits, breakers, fixtures, tolerances, repair

Each at 2 postings / 2 companies. These are real welding and electrical terms and they signal a genuine vocabulary the taxonomy lacks, but as bare nouns at minimum evidence they would produce false matches. **Revisit when trades has 40+ postings and run a tag-only pass.**

**Weak or miscategorised:** Mobile data collection tools, Data Collection (merge group #0 rejected, both too weak at 2p/2c), Data-driven Decision Making (soft, not analytics), Technical proposals, RFQs (2p each, procurement terms worth revisiting later), Cybersecurity (check v2, likely already present)

---

## 5. Honest projection

| Domain | Substantive entries gained | Expected skill_substantive movement |
|---|---|---|
| healthcare | 11 | **Large.** Was 100% ≤1. |
| logistics_supply_chain | 5 | **Large.** |
| trades_technical | 8 | Moderate. |
| sales_marketing | 12 | Moderate, already well covered. |
| engineering | 4 | Moderate. |
| technology_it | 5 | Small, already covered. |
| education | 3 | Small. |
| customer_support_bpo | 3 | **Minimal.** 18 of 25 candidates are genuinely soft. |
| admin_clerical | 3 | **Minimal.** 25 of 30 are soft or clerical. |

**Disclose this on /methodology, do not hide it:** admin_clerical and customer_support_bpo postings describe workplace qualities (attention to detail, multitasking, empathy) rather than named competencies. That is a property of how those roles are advertised in this market, not a limitation of the taxonomy. Their skill_substantive figures will stay low permanently and that is the correct measurement.

---

## 6. Build order

1. Add the 7 new categories to taxonomy_v3.yaml, with requirement_type on every entry
2. Add approved substantive entries with aliases
3. Add approved soft and office_admin entries
4. Apply the 1 approved merge group (EHR)
5. Check for collisions with existing v2 entries before insert (Excel, Cybersecurity, networking terms)
6. Re-extract with extraction_method='taxonomy_v3', preserving v1 and v2 mention rows
7. Re-run the split depth metric, report skill_substantive before and after per domain
8. Update /methodology with the measured figures and the soft-skill disclosure
