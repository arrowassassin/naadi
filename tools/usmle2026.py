#!/usr/bin/env python3
"""Build the USMLE-2026 taxonomy, its blueprint and the legacy crosswalk (segment 23, S23-0).

    python tools/usmle2026.py            # rewrite data/taxonomy/usmle2026.json + usmle2026_question_map.json
    python tools/usmle2026.py --check    # exit 1 when the committed files differ from a fresh build (CI drift guard)

Inputs (read at build time only):
  * docs/research/usmle_outline_and_text_licences.md, section "A.7 (continued)": the 2026 Content Outline tree
    (18 systems, 147 categories, 276 level-3 areas) parsed from the official PDF. The JSON stores only SHORT area
    names derived here (A.6: never the verbatim level-4 example lists) plus a citation to the outline source row.
  * The weight tables of the same file (A.1, A.2): range % per Step row.
  * data/taxonomy.json, data/question_taxonomy_map.json and data/questions.json: the legacy 217-node taxonomy and
    the 685 shipped questions, mapped onto level-3 areas through CROSSWALK below plus keyword scoring.

Outputs:
  * data/taxonomy/usmle2026.json: {outlineVersion, sourceId, nodes[], blueprint[], crosswalk{}}.
  * data/taxonomy/usmle2026_question_map.json: {questionId: [USMLE-2026 node ids]} for the legacy questions.

Blueprint rule (docs/segments/23-content-library.md): a Step's items (2,000) are split over its weight rows by
the row midpoint (normalised so the rows sum to 2,000), each row's share is split evenly over the level-3 areas of
the outline systems in that row (largest remainder), and every area tested on that Step gets at least 6.
Deterministic: same inputs, byte-identical output. Standard library only.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "docs" / "research" / "usmle_outline_and_text_licences.md"
DATA = ROOT / "data"
OUT_DIR = DATA / "taxonomy"
OUT_TAXONOMY = OUT_DIR / "usmle2026.json"
OUT_QMAP = OUT_DIR / "usmle2026_question_map.json"

VERSION = "USMLE-2026"
PREFIX = "usmle2026"
SOURCE_ID = "usmle-content-outline-2026"
ITEMS_PER_STEP = 2000
MIN_PER_AREA = 6
STEPS = ("step1", "step2ck")

# Short names where the automatic rule (below) would keep an example list or lose the meaning.
SHORT_OVERRIDES: dict[str, str] = {
    "05.02.01": "Benign neoplasms",
    "05.02.02": "Malignant neoplasms",
    "05.02.03": "Metastatic neoplasms",
    "07.02.01": "Benign neoplasms",
    "07.02.02": "Malignant and metastatic bone neoplasms",
    "09.02.01": "Benign neoplasms",
    "16.07.01": "Fluid volume and electrolyte disorders",
    "16.07.02": "Acid-base disorders",
    "16.08.01": "Child abuse and nonaccidental trauma",
    "16.08.02": "Intimate partner abuse",
    "16.08.03": "Elder abuse",
    "17.02.01": "Descriptive studies",
    "17.02.05": "Sampling, controls, and randomization",
    "17.04.01": "Distributions of data",
    "17.05.01": "Correlation and regression",
    "17.06.02": "Sensitivity, specificity, and predictive values",
    "17.07.04": "Bias, confounding, and threats to validity",
    "17.07.06": "Statistical vs clinical significance",
    "17.08.01": "Applying evidence to patient care",
    "18.01.01": "Patient-centered interviewing and family interactions",
    "18.02.01": "Informed consent to treatment",
    "18.02.07": "Physician-patient relationship and confidentiality",
    "18.02.09": "Negligence and malpractice",
    "18.02.10": "Physician misconduct",
    "18.02.08": "Impaired physician",
}
CATEGORY_OVERRIDES: dict[str, str] = {
    "03.03": "Anemia, cytopenias, and polycythemia",
    "17.02": "Study design and selection of studies",
    "17.08": "Clinical decision making and evidence-based data",
    "18.01": "Communication and interpersonal skills",
    "18.02": "Medical ethics and jurisprudence",
    "18.03": "Systems-based practice and patient safety",
}
SYSTEM_OVERRIDES: dict[str, str] = {
    "17": "Biostatistics, Epidemiology/Population Health & Medical Literature",
}

# Step weight rows (research A.1, A.2): (row id, label, min %, max %, outline systems in the row).
WEIGHT_ROWS: dict[str, list[tuple[str, str, float, float, tuple[str, ...]]]] = {
    "step1": [
        ("human_development", "Human Development", 1, 3, ("01",)),
        ("blood_immune", "Blood & Lymphoreticular/Immune Systems", 9, 13, ("02", "03")),
        ("behavioral_nervous", "Behavioral Health & Nervous Systems/Special Senses", 10, 14, ("04", "05")),
        ("msk_skin", "Musculoskeletal, Skin & Subcutaneous Tissue", 8, 12, ("06", "07")),
        ("cardiovascular", "Cardiovascular System", 7, 11, ("08",)),
        ("respiratory_renal", "Respiratory & Renal/Urinary Systems", 11, 15, ("09", "11")),
        ("gastrointestinal", "Gastrointestinal System", 6, 10, ("10",)),
        # Pregnancy (12) has no explicit Step 1 row; research A.7 places it here (an inference, documented there).
        ("reproductive_endocrine", "Reproductive & Endocrine Systems", 12, 16, ("12", "13", "14", "15")),
        ("multisystem", "Multisystem Processes & Disorders", 8, 12, ("16",)),
        ("biostat_epi", "Biostatistics & Epidemiology/Population Health", 4, 6, ("17",)),
        ("social_sciences", "Social Sciences: Communication and Interpersonal Skills", 6, 9, ("18",)),
    ],
    "step2ck": [
        ("social_sciences", "Social Sciences: Legal/Ethical, Professionalism, SBP & Patient Safety", 10, 15, ("18",)),
        ("renal_reproductive", "Renal & Urinary System & Reproductive Systems", 7, 13, ("11", "13", "14")),
        ("cardiovascular", "Cardiovascular System", 6, 12, ("08",)),
        ("msk_skin", "Musculoskeletal System/Skin & Subcutaneous Tissue", 6, 12, ("06", "07")),
        ("behavioral_health", "Behavioral Health", 5, 10, ("04",)),
        ("blood_immune", "Blood & Lymphoreticular/Immune Systems", 5, 10, ("02", "03")),
        ("gastrointestinal", "Gastrointestinal System", 5, 10, ("10",)),
        ("nervous", "Nervous System & Special Senses", 5, 10, ("05",)),
        ("respiratory", "Respiratory System", 5, 10, ("09",)),
        ("multisystem", "Multisystem Processes & Disorders", 4, 8, ("16",)),
        ("endocrine", "Endocrine System", 3, 7, ("15",)),
        ("pregnancy", "Pregnancy, Childbirth & the Puerperium", 3, 7, ("12",)),
        ("biostat_epi", "Biostatistics, Epidemiology/Population Health & Medical Literature", 3, 5, ("17",)),
        ("human_development", "Human Development", 2, 4, ("01",)),
    ],
}

# Orthogonal axes: (slug, label, {step: (min %, max %)}). Items are co-coded, so totals exceed 100 %.
DISCIPLINES: list[tuple[str, str, dict[str, tuple[float, float]]]] = [
    ("pathology", "Pathology", {"step1": (45, 55)}),
    ("physiology", "Physiology", {"step1": (30, 40)}),
    ("nutrition", "Nutrition", {"step1": (15, 20), "step2ck": (15, 20)}),
    ("anatomy_embryology", "Gross Anatomy & Embryology", {"step1": (10, 20)}),
    ("microbiology", "Microbiology", {"step1": (10, 20)}),
    ("pharmacology", "Pharmacology", {"step1": (10, 20)}),
    ("behavioral_sciences", "Behavioral Sciences", {"step1": (10, 15)}),
    ("biochemistry", "Biochemistry", {"step1": (5, 15)}),
    ("histology_cell_biology", "Histology & Cell Biology", {"step1": (5, 15)}),
    ("immunology", "Immunology", {"step1": (5, 15)}),
    ("genetics", "Genetics", {"step1": (5, 10)}),
]
CLINICAL_SCIENCES: list[tuple[str, str, dict[str, tuple[float, float]]]] = [
    ("medicine", "Medicine", {"step2ck": (55, 65)}),
    ("pediatrics", "Pediatrics", {"step2ck": (17, 27)}),
    ("obgyn", "Obstetrics & Gynecology", {"step2ck": (10, 20)}),
    ("psychiatry", "Psychiatry", {"step2ck": (10, 15)}),
    ("surgery", "Surgery", {"step2ck": (5, 15)}),
]
TASKS: list[tuple[str, str, dict[str, tuple[float, float]]]] = [
    ("medical_knowledge", "Medical Knowledge: Applying Foundational Science Concepts", {"step1": (60, 70)}),
    ("pc_history_pe", "Patient Care: History and Physical Examination", {}),
    ("pc_diagnosis", "Patient Care: Diagnosis", {"step1": (20, 25), "step2ck": (16, 20)}),
    ("pc_lab_diagnostic", "Patient Care: Laboratory/Diagnostic Studies", {"step2ck": (13, 17)}),
    ("pc_prognosis", "Patient Care: Prognosis/Outcome", {"step2ck": (5, 9)}),
    ("pc_health_maintenance", "Patient Care: Health Maintenance/Disease Prevention", {"step2ck": (5, 10)}),
    ("pc_pharmacotherapy", "Patient Care: Pharmacotherapy", {"step2ck": (8, 12)}),
    ("pc_clinical_interventions", "Patient Care: Clinical Interventions", {"step2ck": (6, 10)}),
    ("pc_mixed_management", "Patient Care: Mixed Management", {"step2ck": (12, 16)}),
    ("communication", "Communication and Interpersonal Skills", {"step1": (6, 9)}),
    ("professionalism", "Professionalism, including Legal and Ethical Issues", {"step2ck": (5, 7)}),
    ("systems_based_practice", "Systems-based Practice, including Patient Safety", {"step2ck": (5, 7)}),
    ("pbli", "Practice-based Learning & Improvement", {"step1": (4, 6), "step2ck": (3, 5)}),
]

# Legacy axis ids -> USMLE-2026 axis ids.
LEGACY_AXES: dict[str, str] = {
    **{f"step1.disc.{s}": f"{PREFIX}.disc.{s}" for s, _l, _w in DISCIPLINES},
    "step2ck.sys.nutrition": f"{PREFIX}.disc.nutrition",
    "topic.nutrition.vitamins_minerals": f"{PREFIX}.disc.nutrition",
    **{f"step2ck.clin.{s}": f"{PREFIX}.cs.{s}" for s, _l, _w in CLINICAL_SCIENCES},
    "step1.comp.medical_knowledge": f"{PREFIX}.task.medical_knowledge",
    "step1.comp.pc_diagnosis": f"{PREFIX}.task.pc_diagnosis",
    "step1.comp.pc_diagnosis.history_pe": f"{PREFIX}.task.pc_history_pe",
    "step1.comp.pc_diagnosis.diagnosis": f"{PREFIX}.task.pc_diagnosis",
    "step1.comp.communication": f"{PREFIX}.task.communication",
    "step1.comp.pbli": f"{PREFIX}.task.pbli",
    "step2ck.comp.pc_diagnosis": f"{PREFIX}.task.pc_diagnosis",
    "step2ck.comp.pc_lab_diagnostic": f"{PREFIX}.task.pc_lab_diagnostic",
    "step2ck.comp.pc_mixed_management": f"{PREFIX}.task.pc_mixed_management",
    "step2ck.comp.pc_pharmacotherapy": f"{PREFIX}.task.pc_pharmacotherapy",
    "step2ck.comp.pc_clinical_interventions": f"{PREFIX}.task.pc_clinical_interventions",
    "step2ck.comp.pc_health_maintenance": f"{PREFIX}.task.pc_health_maintenance",
    "step2ck.comp.pc_prognosis": f"{PREFIX}.task.pc_prognosis",
    "step2ck.comp.systems_based_practice": f"{PREFIX}.task.systems_based_practice",
    "step2ck.comp.professionalism": f"{PREFIX}.task.professionalism",
    "step2ck.comp.pbli": f"{PREFIX}.task.pbli",
    "step2ck.comp.medical_knowledge": f"{PREFIX}.task.medical_knowledge",
    "step2ck.comp.pc_history_pe": f"{PREFIX}.task.pc_history_pe",
}

# Legacy topic -> candidate outline codes (system "08", category "08.05" or area "08.05.01"). A question tagged with
# the topic is mapped to the best-scoring level-3 area under these codes (keyword overlap with the area text).
CROSSWALK: dict[str, tuple[str, ...]] = {
    "topic.dev.milestones": ("01.01.01",),
    "topic.dev.growth_puberty": ("01.01.01", "01.01.02"),
    "topic.dev.neonatal": ("12.05", "01.01.01"),
    "topic.dev.adolescent": ("01.01.02",),
    "topic.dev.aging": ("01.01.04",),
    "topic.dev.well_care": ("01.01",),
    "topic.heme.anemia": ("03.03",),
    "topic.heme.hemolysis_hemoglobinopathy": ("03.03.02", "03.03.03", "03.05.01"),
    "topic.heme.hemostasis": ("03.04",),
    "topic.heme.anticoagulation_vte": ("03.04.02", "03.06"),
    "topic.heme.heme_malignancy": ("03.02",),
    "topic.heme.transfusion": ("03.04.03",),
    "topic.heme.heme_onc_pharm": ("03.06",),
    "topic.immune.immune_basics": ("02.01", "02.03", "03.01.03"),
    "topic.immune.hypersensitivity": ("02.03.01",),
    "topic.immune.immunodeficiency": ("02.01", "02.02"),
    "topic.immune.transplant_immunosuppression": ("02.03.02", "02.04"),
    "topic.immune.vaccines": ("02.04", "01.01"),
    "topic.neuro.stroke_hemorrhage": ("05.03", "05.14"),
    "topic.neuro.localization": ("05.04", "05.05", "05.03"),
    "topic.neuro.seizures": ("05.12",),
    "topic.neuro.headache": ("05.12", "05.06"),
    "topic.neuro.neurodegenerative_demyelinating": ("05.07", "05.10", "05.01.02", "05.09"),
    "topic.neuro.neuromuscular": ("05.09", "05.05.02", "05.01.02"),
    "topic.neuro.tumors_icp_coma": ("05.02", "05.08", "05.14"),
    "topic.neuro.special_senses": ("05.17", "05.18"),
    "topic.psych.mood": ("04.03",),
    "topic.psych.psychosis": ("04.01", "05.16"),
    "topic.psych.anxiety_trauma_ocd": ("04.02",),
    "topic.psych.substance_use": ("04.11",),
    "topic.psych.personality_somatic_defense": ("04.08", "04.04", "04.05", "04.09"),
    "topic.psych.child_eating": ("04.07", "04.06"),
    "topic.psych.sleep_suicide": ("05.13", "04.03", "18.02.03"),
    "topic.msk.limb_anatomy_nerve_injury": ("05.05.02", "07.04"),
    "topic.msk.arthritis_crystal": ("07.01", "07.03"),
    "topic.msk.autoimmune_ctd": ("07.01", "16.01.02"),
    "topic.msk.bone": ("07.03", "07.02"),
    "topic.msk.orthopedics_trauma": ("07.04",),
    "topic.msk.muscle_disorders": ("05.09", "07.03"),
    "topic.msk.msk_pharm": ("07.06",),
    "topic.derm.blistering": ("06.01",),
    "topic.derm.skin_cancer": ("06.02",),
    "topic.derm.inflammatory_dermatoses": ("06.01", "06.03"),
    "topic.derm.skin_infections_drug_eruptions": ("06.01", "06.08"),
    "topic.derm.burns_wounds": ("06.06",),
    "topic.cardio.embryology_congenital": ("08.14",),
    "topic.cardio.physiology_hemodynamics": ("08.04", "08.08", "08.09", "08.10"),
    "topic.cardio.ischemic_heart_disease": ("08.05",),
    "topic.cardio.heart_failure_cardiomyopathy": ("08.04", "08.06"),
    "topic.cardio.arrhythmia": ("08.03",),
    "topic.cardio.valvular": ("08.08",),
    "topic.cardio.hypertension": ("08.10",),
    "topic.cardio.lipids_atherosclerosis": ("08.11", "08.12"),
    "topic.cardio.endocarditis_pericardial": ("08.01", "08.07"),
    "topic.cardio.vascular_aorta": ("08.12", "08.13"),
    "topic.cardio.vasculitis": ("08.01", "08.12"),
    "topic.cardio.shock_syncope": ("16.10", "08.09"),
    "topic.cardio.cardio_pharm": ("08.15",),
    "topic.resp.physiology": ("09.06",),
    "topic.resp.obstructive": ("09.03",),
    "topic.resp.restrictive_ild": ("09.04", "09.07"),
    "topic.resp.pulmonary_vascular": ("09.05",),
    "topic.resp.lung_cancer_nodules": ("09.02",),
    "topic.resp.pneumonia_infection": ("09.01",),
    "topic.resp.pleural": ("09.07",),
    "topic.resp.critical_care_ventilation": ("09.05", "09.06", "05.13"),
    "topic.renal.physiology": ("11.04",),
    "topic.renal.acid_base_electrolytes": ("16.07",),
    "topic.renal.glomerular": ("11.01",),
    "topic.renal.aki_ckd": ("11.04", "11.05"),
    "topic.renal.stones_urinary_tract": ("11.06", "11.01", "11.03"),
    "topic.renal.cystic_tumors": ("11.02", "11.07"),
    "topic.renal.renal_pharm": ("11.08", "16.12"),
    "topic.gi.embryology_anatomy": ("10.10",),
    "topic.gi.physiology_hormones": ("10.06", "10.05"),
    "topic.gi.esophagus_stomach": ("10.04", "10.05"),
    "topic.gi.malabsorption_ibd": ("10.05", "10.01"),
    "topic.gi.liver": ("10.06", "10.01"),
    "topic.gi.biliary_pancreas": ("10.06", "10.07"),
    "topic.gi.gi_bleeding": ("10.05", "10.04"),
    "topic.gi.colorectal": ("10.02", "10.05"),
    "topic.gi.acute_abdomen": ("10.09", "10.08", "10.03"),
    "topic.gi.gi_pharm": ("10.11",),
    "topic.endo.diabetes": ("15.01",),
    "topic.endo.thyroid": ("15.02",),
    "topic.endo.adrenal": ("15.04", "15.08"),
    "topic.endo.pituitary": ("15.05", "15.06"),
    "topic.endo.calcium_parathyroid": ("15.03",),
    "topic.endo.men_neuroendocrine": ("15.07",),
    "topic.repro.menstrual_amenorrhea_pcos": ("13.02.05", "15.08"),
    "topic.repro.contraception_infertility_menopause": ("13.02.03", "13.02.04", "13.02.09"),
    "topic.repro.gyn_oncology": ("13.02.02",),
    "topic.repro.breast": ("13.01",),
    "topic.repro.vaginitis_pid_sti": ("13.02.01", "14.01"),
    "topic.male.male_repro": ("14",),
    "topic.preg.prenatal_physiology": ("12.01",),
    "topic.preg.pregnancy_complications": ("12.02", "12.07"),
    "topic.preg.labor_postpartum": ("12.03", "12.04"),
    "topic.multi.bacteria": ("16.01.01",),
    "topic.multi.viruses_hiv": ("16.01.01", "02.02"),
    "topic.multi.fungi_parasites": ("16.01.01",),
    "topic.multi.antimicrobials": ("16.01.01", "16.12"),
    "topic.multi.sepsis_systemic_infection": ("16.10", "16.01.01"),
    "topic.multi.neoplasia": ("16.02",),
    "topic.multi.cell_injury_inflammation": ("16.01.02", "16.03"),
    "topic.multi.genetics": ("16.11",),
    "topic.multi.metabolism_inborn_errors": ("16.11.03",),
    "topic.multi.molecular_cell_biology": ("16.11",),
    "topic.nutrition.vitamins_minerals": ("16.04",),
    "topic.multi.pharm_principles": ("16.12",),
    "topic.multi.toxicology_environmental": ("16.05", "16.06"),
    "topic.multi.trauma_surgery_periop": ("16.09", "16.10"),
    "topic.biostat.diagnostic_tests": ("17.06",),
    "topic.biostat.risk_measures": ("17.01.01", "17.03"),
    "topic.biostat.study_design": ("17.02",),
    "topic.biostat.bias_confounding": ("17.07.04",),
    "topic.biostat.hypothesis_testing": ("17.07.02", "17.04"),
    "topic.biostat.prevention_screening": ("17.01.08", "17.06.01"),
    "topic.biostat.ebm_literature": ("17.08", "17.07"),
    "topic.social.capacity_consent": ("18.02.01", "18.02.02"),
    "topic.social.confidentiality_reporting": ("18.02.07", "18.02.04"),
    "topic.social.minors": ("18.02.01",),
    "topic.social.end_of_life_surrogates": ("18.02.06",),
    "topic.social.communication": ("18.01",),
    "topic.social.patient_safety_qi": ("18.03",),
    "topic.social.professionalism": ("18.02.08", "18.02.10", "18.02.07"),
    "topic.social.legal_health_systems": ("18.04", "18.02.09"),
}

# Legacy system id (both Steps) -> outline systems, the fallback when a question has no topic.
LEGACY_SYSTEMS: dict[str, tuple[str, ...]] = {
    "human_development": ("01",),
    "blood_immune": ("02", "03"),
    "behavioral_nervous": ("04", "05"),
    "behavioral_health": ("04",),
    "nervous": ("05",),
    "msk_skin": ("06", "07"),
    "cardiovascular": ("08",),
    "respiratory_renal": ("09", "11"),
    "respiratory": ("09",),
    "renal_reproductive": ("11", "13", "14"),
    "gastrointestinal": ("10",),
    "reproductive_endocrine": ("12", "13", "14", "15"),
    "endocrine": ("15",),
    "pregnancy": ("12",),
    "multisystem": ("16",),
    "biostat_epi": ("17",),
    "social_sciences": ("18",),
}

# Build-time keyword hints (Naadi's own words) that sharpen the keyword scoring for the legacy mapping. Never stored.
AREA_HINTS: dict[str, str] = {
    "02.01.01": "bruton agammaglobulinemia iga deficiency cvid hyper-igm btk b-cell",
    "02.01.02": "digeorge scid wiskott-aldrich ataxia telangiectasia t-cell thymic",
    "02.01.04": "chronic granulomatous nadph oxidase leukocyte adhesion chediak neutrophil",
    "02.03.01": "anaphylaxis ige type serum sickness immune complex contact dermatitis hypersensitivity",
    "02.03.02": "graft rejection transplant hla host",
    "02.04.01": "cyclosporine tacrolimus azathioprine methotrexate rituximab infliximab monoclonal vaccine",
    "03.03.01": "iron deficiency b12 folate megaloblastic aplastic marrow sideroblastic anemia chronic disease",
    "03.03.02": "hemolysis hemolytic coombs autoimmune g6pd haptoglobin schistocytes",
    "03.03.03": "sickle thalassemia spherocytosis porphyria hemoglobin membrane",
    "03.04.01": "bleeding hemophilia willebrand itp thrombocytopenia dic vitamin coagulopathy",
    "03.04.02": "thrombosis thrombophilia leiden antiphospholipid protein heparin-induced hit clot dvt embolism",
    "03.04.03": "transfusion reaction blood products plasma platelets cryoprecipitate trali",
    "03.06.01": "warfarin heparin dabigatran clopidogrel aspirin chemotherapy methemoglobinemia tumor lysis",
    "04.11.01": "alcohol withdrawal opioid cocaine nicotine cannabis benzodiazepine intoxication naloxone",
    "05.03.01": "stroke aneurysm subarachnoid hemorrhage carotid infarct tia",
    "05.07.01": "dementia alzheimer frontotemporal lewy memory",
    "05.09.01": "myasthenia lambert-eaton als duchenne myotonic dystrophy",
    "05.12.01": "seizure epilepsy migraine headache cluster tension status",
    "07.03.02": "gout pseudogout osteoarthritis urate crystal allopurinol colchicine febuxostat",
    "07.01.02": "lupus rheumatoid scleroderma sjogren dermatomyositis spondylitis",
    "08.05.01": "myocardial infarction angina coronary troponin stemi",
    "08.12.01": "aortic dissection aneurysm aorta",
    "10.05.02": "diverticulitis diverticulosis appendicitis crohn colitis celiac malabsorption obstruction ischemia",
    "10.06.01": "cirrhosis hepatitis jaundice bilirubin hemochromatosis wilson liver",
    "10.06.02": "gallstone cholecystitis cholangitis biliary",
    "15.02.01": "thyroid hyperthyroidism hypothyroidism graves hashimoto thyroiditis nodule medullary",
    "15.07": "men1 men2 multiple endocrine neoplasia ret medullary pheochromocytoma",
    "15.09.01": "glucocorticoid steroid suppression perioperative amiodarone lithium",
    "16.01.01": "bacteria virus fungus parasite antibiotic infection culture gram",
    "16.11.01": "multifactorial",
    "16.11.02": "down trisomy deletion translocation chromosome",
    "16.11.03": "enzyme lysosomal storage glycogen urea cycle phenylketonuria inborn",
    "16.11.04": "collagen ehlers-danlos marfan osteogenesis fibrillin",
    "16.11.05": "cystic fibrosis cftr receptor channel transport",
    "16.11.06": "trinucleotide repeat fragile huntington myotonic",
    "16.12.01": "drug-induced pharmacokinetics pharmacodynamics receptor agonist antagonist half-life clearance",
}

_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "or",
        "of",
        "the",
        "in",
        "on",
        "to",
        "for",
        "with",
        "without",
        "by",
        "from",
        "as",
        "at",
        "is",
        "are",
        "be",
        "eg",
        "including",
        "include",
        "includes",
        "other",
        "disorders",
        "disorder",
        "disease",
        "diseases",
        "related",
        "due",
        "not",
        "otherwise",
        "specified",
        "nos",
        "its",
        "their",
    ]
)


def _words(text: str) -> list[str]:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text)).lower()
    return [w for w in re.findall(r"[a-z][a-z0-9-]{2,}", text) if w not in _STOP]


def _strip(text: str) -> str:
    text = re.sub(r"\s*\(eg,[^)]*\)", "", text)
    text = re.sub(r",? including microbiologic and host response to insult", "", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(",;:")
    return text[:1].upper() + text[1:]


def parse_outline(
    path: Path = RESEARCH,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str, int]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("## A.7 (continued)"))
    systems: list[tuple[str, str]] = []
    cats: list[tuple[str, str]] = []
    areas: list[tuple[str, str, int]] = []
    for ln in lines[start:]:
        if m := re.match(r"^- `(\d\d)` \*\*(.+)\*\*$", ln):
            systems.append((m.group(1), m.group(2)))
        elif m := re.match(r"^  - `(\d\d\.\d\d)` (.+)$", ln):
            cats.append((m.group(1), m.group(2)))
        elif m := re.match(r"^    - `(\d\d\.\d\d\.\d\d)` (.+)$", ln):
            text = m.group(2)
            ex = re.search(r"_\(\+(\d+) example lines? in PDF\)_", text)
            areas.append((m.group(1), re.sub(r"\s*_\(\+.*\)_\s*$", "", text), int(ex.group(1)) if ex else 0))
    if (len(systems), len(cats), len(areas)) != (18, 147, 276):
        raise SystemExit(f"outline parse: {len(systems)} systems / {len(cats)} categories / {len(areas)} areas")
    return systems, cats, areas


def short_area(code: str, text: str, category_label: str, siblings: int) -> str:
    if code in SHORT_OVERRIDES:
        return SHORT_OVERRIDES[code]
    s = _strip(text)
    if ";" in s or len(s) > 70:
        if siblings == 1:
            return category_label
        s = _strip(re.split(r"[;:]", s)[0])
    return s if len(s) <= 80 else s[:77].rsplit(" ", 1)[0] + "…"


def build_nodes() -> tuple[list[dict[str, object]], dict[str, str]]:
    systems, cats, areas = parse_outline()
    siblings = Counter(code[:5] for code, _t, _e in areas)
    nodes: list[dict[str, object]] = [
        {
            "id": PREFIX,
            "kind": "outline",
            "parent_id": None,
            "code": None,
            "level": 0,
            "label": "USMLE Content Outline 2026",
            "exam": list(STEPS),
            "sort": 0,
        }
    ]
    cat_labels: dict[str, str] = {}
    full_text: dict[str, str] = {}  # build-time only (keyword scoring); never written
    for i, (code, label) in enumerate(systems):
        nodes.append(
            {
                "id": f"{PREFIX}.{code}",
                "kind": "outline_system",
                "parent_id": PREFIX,
                "code": code,
                "level": 1,
                "label": SYSTEM_OVERRIDES.get(code, label),
                "exam": list(STEPS),
                "sort": i + 1,
            }
        )
    for i, (code, label) in enumerate(cats):
        cat_labels[code] = CATEGORY_OVERRIDES.get(code) or _strip(label)
        nodes.append(
            {
                "id": f"{PREFIX}.{code}",
                "kind": "outline_category",
                "parent_id": f"{PREFIX}.{code[:2]}",
                "code": code,
                "level": 2,
                "label": cat_labels[code],
                "exam": list(STEPS),
                "sort": i + 1,
            }
        )
    for i, (code, text, examples) in enumerate(areas):
        full_text[code] = f"{text} {dict(cats)[code[:5]]} {AREA_HINTS.get(code, '')}"
        nodes.append(
            {
                "id": f"{PREFIX}.{code}",
                "kind": "outline_area",
                "parent_id": f"{PREFIX}.{code[:5]}",
                "code": code,
                "level": 3,
                "label": short_area(code, text, cat_labels[code[:5]], siblings[code[:5]]),
                "exam": list(STEPS),
                "sort": i + 1,
                "exampleLines": examples,
                "leaf": True,
            }
        )
    # Categories with no level-3 entry (15.06 hypothalamic, 15.07 MEN) are leaves too: items tag them directly.
    with_areas = {code[:5] for code, _t, _e in areas}
    for code, label in cats:
        if code not in with_areas:
            full_text[code] = f"{label} {AREA_HINTS.get(code, '')}"
            next(n for n in nodes if n["code"] == code)["leaf"] = True
    for kind, axis, rows in (
        ("discipline", "disc", DISCIPLINES),
        ("clinical_science", "cs", CLINICAL_SCIENCES),
        ("task", "task", TASKS),
    ):
        for i, (slug, label, weights) in enumerate(rows):
            nodes.append(
                {
                    "id": f"{PREFIX}.{axis}.{slug}",
                    "kind": kind,
                    "parent_id": PREFIX,
                    "code": None,
                    "level": None,
                    "label": label,
                    "exam": sorted(weights) or list(STEPS),
                    "sort": 100 + i,
                }
            )
    return nodes, full_text


def _largest_remainder(total: int, n: int) -> list[int]:
    base, extra = divmod(total, n)
    return [base + (1 if i < extra else 0) for i in range(n)]


def build_blueprint(nodes: list[dict[str, object]]) -> list[dict[str, object]]:
    area_ids = sorted(str(n["code"]) for n in nodes if n.get("leaf"))
    out: list[dict[str, object]] = []
    for step in STEPS:
        rows = WEIGHT_ROWS[step]
        mids = [(lo + hi) / 2 for _r, _l, lo, hi, _s in rows]
        raw = [m / sum(mids) * ITEMS_PER_STEP for m in mids]
        shares = [int(x) for x in raw]
        for i in sorted(range(len(raw)), key=lambda i: (-(raw[i] - shares[i]), i))[: ITEMS_PER_STEP - sum(shares)]:
            shares[i] += 1
        covered: set[str] = set()
        for (row, label, lo, hi, systems), share in zip(rows, shares, strict=True):
            codes = [c for c in area_ids if c[:2] in systems]
            covered |= set(codes)
            for code, n in zip(codes, _largest_remainder(share, len(codes)), strict=True):
                out.append(
                    {
                        "nodeId": f"{PREFIX}.{code}",
                        "exam": step,
                        "row": row,
                        "rowLabel": label,
                        "weightMin": lo,
                        "weightMax": hi,
                        "share": n,
                        "minItems": MIN_PER_AREA,
                        "targetItems": max(n, MIN_PER_AREA),
                        "tested": True,
                    }
                )
        missing = sorted(set(area_ids) - covered)
        if missing:
            raise SystemExit(f"{step}: areas in no weight row: {missing[:5]}")
        for axis, rows_ in (("disc", DISCIPLINES), ("cs", CLINICAL_SCIENCES), ("task", TASKS)):
            for slug, label, weights in rows_:
                if step not in weights:
                    continue
                lo, hi = weights[step]
                n = round((lo + hi) / 2 / 100 * ITEMS_PER_STEP)
                out.append(
                    {
                        "nodeId": f"{PREFIX}.{axis}.{slug}",
                        "exam": step,
                        "row": f"{axis}.{slug}",
                        "rowLabel": label,
                        "weightMin": lo,
                        "weightMax": hi,
                        "share": n,
                        "minItems": 0,
                        "targetItems": n,
                        "tested": True,
                    }
                )
    return out


def _candidates(codes: tuple[str, ...], area_codes: list[str]) -> list[str]:
    return [a for a in area_codes if any(a == c or a.startswith(c + ".") for c in codes)]


def map_questions(full_text: dict[str, str]) -> tuple[dict[str, list[str]], dict[str, int]]:
    questions = json.loads((DATA / "questions.json").read_text(encoding="utf-8"))
    legacy = json.loads((DATA / "question_taxonomy_map.json").read_text(encoding="utf-8"))
    area_codes = list(full_text)
    area_words = {c: set(_words(t)) for c, t in full_text.items()}
    doc_freq = Counter(w for ws in area_words.values() for w in ws)
    stats: Counter[str] = Counter()
    out: dict[str, list[str]] = {}
    for q in questions:
        tags = legacy.get(q["id"], [])
        topics = [t for t in tags if t in CROSSWALK]
        cands: list[str] = []
        for t in topics:
            cands += [c for c in _candidates(CROSSWALK[t], area_codes) if c not in cands]
        if not cands:
            for t in tags:
                parts = t.split(".")
                if len(parts) == 3 and parts[1] == "sys" and parts[2] in LEGACY_SYSTEMS:
                    cands += _candidates(LEGACY_SYSTEMS[parts[2]], area_codes)
            stats["system_fallback"] += 1
        if not cands:
            cands = area_codes
            stats["no_system"] += 1
        text = " ".join(
            [
                " ".join(q.get("tags") or []) * 3,
                q["stemHtml"],
                q.get("explanationHtml") or "",
                q.get("answerHtml") or "",
            ]
        )
        counts = Counter(_words(text))

        def score(code: str, counts: Counter[str] = counts) -> tuple[float, int]:
            s = sum(min(counts[w], 3) / doc_freq[w] for w in area_words[code] if w in counts)
            return (s, -area_codes.index(code))

        best = max(cands, key=score)
        ids = [f"{PREFIX}.{best}"]
        # A second area only when another topic of the question points elsewhere and scores well there.
        for t in topics[1:2]:
            alt = [c for c in _candidates(CROSSWALK[t], area_codes) if c != best]
            if alt:
                second = max(alt, key=score)
                if score(second)[0] >= 0.5 * score(best)[0] > 0:
                    ids.append(f"{PREFIX}.{second}")
        for t in tags:
            if t in LEGACY_AXES and LEGACY_AXES[t] not in ids:
                ids.append(LEGACY_AXES[t])
        stats["mapped"] += 1
        stats["two_areas"] += len([i for i in ids if i.count(".") == 3]) > 1
        out[q["id"]] = ids
    return out, dict(stats)


def build() -> tuple[str, str, dict[str, int]]:
    nodes, full_text = build_nodes()
    blueprint = build_blueprint(nodes)
    qmap, stats = map_questions(full_text)
    crosswalk = {t: [f"{PREFIX}.{c}" for c in codes] for t, codes in sorted(CROSSWALK.items())}
    crosswalk |= {k: [v] for k, v in sorted(LEGACY_AXES.items()) if k not in crosswalk}
    doc = {
        "outlineVersion": VERSION,
        "sourceId": SOURCE_ID,
        "generator": "python tools/usmle2026.py",
        "note": (
            "Short area names only, derived from the USMLE Content Outline 2026 (FSMB/NBME, all rights reserved;"
            " see sources.yaml usmle-content-outline-2026). The detailed example lists are not reproduced."
        ),
        "nodes": nodes,
        "blueprint": blueprint,
        "crosswalk": crosswalk,
    }
    return (
        json.dumps(doc, indent=1, ensure_ascii=False) + "\n",
        json.dumps(qmap, indent=0, ensure_ascii=False, sort_keys=True) + "\n",
        stats,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--check", action="store_true", help="fail when the committed files are stale")
    args = p.parse_args(argv)
    taxonomy, qmap, stats = build()
    if args.check:
        stale = [
            p.name
            for p, s in ((OUT_TAXONOMY, taxonomy), (OUT_QMAP, qmap))
            if not p.is_file() or p.read_text(encoding="utf-8") != s
        ]
        if stale:
            print(f"stale: {', '.join(stale)}; run `python tools/usmle2026.py` and commit", file=sys.stderr)
            return 1
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_TAXONOMY.write_text(taxonomy, encoding="utf-8")
    OUT_QMAP.write_text(qmap, encoding="utf-8")
    print(f"wrote {OUT_TAXONOMY.relative_to(ROOT)} and {OUT_QMAP.relative_to(ROOT)}: {stats}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
