#!/usr/bin/env python3
"""Naadi taxonomy builder + deterministic question -> taxonomy mapper.

Single source of truth for:
  * the official USMLE outline nodes (exams, systems, disciplines, competencies,
    clinical sciences) with the published % ranges (usmle.org, as captured in
    research_notes/.../usmle_taxonomy_pipeline.md, Nov 2026), and
  * Naadi's internal topic layer (topic.<domain>.<slug>) and its keyword rules.

Usage
  python tools/map_taxonomy.py --emit-taxonomy   # (re)write data/taxonomy.json
  python tools/map_taxonomy.py                   # write data/question_taxonomy_map.json + coverage report
  python tools/map_taxonomy.py --explain ch05-q07

Design notes
  * Systems are Step-specific (groupings differ between Steps), so a question is
    mapped to the system vocabulary of its own `exam`.
  * Topics are exam-agnostic. Each topic belongs to a canonical *domain*
    (cardio, resp, renal, ...). Its parent_id is the Step 1 system node that
    contains that domain; DOMAIN_TO_SYSTEM cross-walks each domain to the
    system node of every Step.
  * Matching is pure regex keyword scoring over tags (x3), stem+options (x1),
    answer + explanation (x0.5). Chapter priors break ties and supply a
    fallback, so output is fully deterministic.
  * Other tools (youtube_mapper.py) import TOPICS / score_topics from here.
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# ─────────────────────────── Official outline (usmle.org) ───────────────────────────
# (id_suffix, label, min, max). None = no published percentage.
STEP1_SYSTEMS = [
    ("human_development", "Human Development (incl. Normal Age-Related Findings, Care of the Well Patient)", 1, 3),
    ("blood_immune", "Blood & Lymphoreticular/Immune Systems", 9, 13),
    ("behavioral_nervous", "Behavioral Health & Nervous Systems/Special Senses", 10, 14),
    ("msk_skin", "Musculoskeletal, Skin & Subcutaneous Tissue", 8, 12),
    ("cardiovascular", "Cardiovascular System", 7, 11),
    ("respiratory_renal", "Respiratory & Renal/Urinary Systems", 11, 15),
    ("gastrointestinal", "Gastrointestinal System", 6, 10),
    ("reproductive_endocrine", "Reproductive & Endocrine Systems", 12, 16),
    ("multisystem", "Multisystem Processes & Disorders", 8, 12),
    ("biostat_epi", "Biostatistics & Epidemiology/Population Health", 4, 6),
    ("social_sciences", "Social Sciences: Communication and Interpersonal Skills", 6, 9),
]
STEP1_COMPETENCIES = [
    ("medical_knowledge", "Medical Knowledge: Applying Foundational Science Concepts", 60, 70, None),
    ("pc_diagnosis", "Patient Care: Diagnosis", 20, 25, None),
    ("pc_diagnosis.history_pe", "History/Physical Examination", None, None, "pc_diagnosis"),
    ("pc_diagnosis.diagnosis", "Diagnosis", None, None, "pc_diagnosis"),
    ("communication", "Communication and Interpersonal Skills", 6, 9, None),
    ("pbli", "Practice-based Learning & Improvement", 4, 6, None),
]
STEP1_DISCIPLINES = [
    ("pathology", "Pathology", 45, 55),
    ("physiology", "Physiology", 30, 40),
    ("nutrition", "Nutrition", 15, 20),
    ("anatomy_embryology", "Gross Anatomy & Embryology", 10, 20),
    ("microbiology", "Microbiology", 10, 20),
    ("pharmacology", "Pharmacology", 10, 20),
    ("behavioral_sciences", "Behavioral Sciences", 10, 15),
    ("biochemistry", "Biochemistry", 5, 15),
    ("histology_cell_biology", "Histology & Cell Biology", 5, 15),
    ("immunology", "Immunology", 5, 15),
    ("genetics", "Genetics", 5, 10),
]
STEP2CK_SYSTEMS = [
    ("nutrition", "Nutrition (co-coded with a system)", 15, 20),
    ("social_sciences", "Social Sciences: Legal/Ethical Issues & Professionalism/Systems-based Practice & Patient Safety", 10, 15),
    ("renal_reproductive", "Renal & Urinary System & Reproductive Systems", 7, 13),
    ("cardiovascular", "Cardiovascular System", 6, 12),
    ("msk_skin", "Musculoskeletal System/Skin & Subcutaneous Tissue", 6, 12),
    ("behavioral_health", "Behavioral Health", 5, 10),
    ("blood_immune", "Blood & Lymphoreticular/Immune Systems", 5, 10),
    ("gastrointestinal", "Gastrointestinal System", 5, 10),
    ("nervous", "Nervous System & Special Senses", 5, 10),
    ("respiratory", "Respiratory System", 5, 10),
    ("multisystem", "Multisystem Processes & Disorders", 4, 8),
    ("endocrine", "Endocrine System", 3, 7),
    ("pregnancy", "Pregnancy, Childbirth & the Puerperium", 3, 7),
    ("biostat_epi", "Biostatistics & Epidemiology/Population Health/Interpretation of Medical Literature", 3, 5),
    ("human_development", "Human Development", 2, 4),
]
STEP2CK_COMPETENCIES = [
    ("pc_diagnosis", "Patient Care: Diagnosis", 16, 20, None),
    ("pc_lab_diagnostic", "Patient Care: Laboratory/Diagnostic Studies", 13, 17, None),
    ("pc_mixed_management", "Patient Care: Mixed Management", 12, 16, None),
    ("pc_pharmacotherapy", "Patient Care: Pharmacotherapy", 8, 12, None),
    ("pc_clinical_interventions", "Patient Care: Clinical Interventions", 6, 10, None),
    ("pc_health_maintenance", "Patient Care: Health Maintenance/Disease Prevention", 5, 10, None),
    ("pc_prognosis", "Patient Care: Prognosis/Outcome", 5, 9, None),
    ("systems_based_practice", "Systems-based Practice & Patient Safety", 5, 7, None),
    ("professionalism", "Professionalism", 5, 7, None),
    ("pbli", "Practice-based Learning & Improvement", 3, 5, None),
    ("medical_knowledge", "Medical Knowledge: Applying Foundational Science Concepts (may be co-assessed)", 0, 0, None),
    ("pc_history_pe", "Patient Care: History and Physical Exam (covered on Step 1 and Step 3)", 0, 0, None),
]
STEP2CK_CLINICAL = [
    ("medicine", "Medicine", 55, 65),
    ("pediatrics", "Pediatrics", 17, 27),
    ("obgyn", "Obstetrics & Gynecology", 10, 20),
    ("psychiatry", "Psychiatry", 10, 15),
    ("surgery", "Surgery", 5, 15),
]
STEP3_SYSTEMS = [
    ("human_development", "Human Development", 1, 3),
    ("immune_blood_multisystem", "Immune System, Blood & Lymphoreticular System, and Multisystem Processes/Disorders", 6, 8),
    ("behavioral_health", "Behavioral Health", 4, 6),
    ("nervous", "Nervous System & Special Senses", 8, 10),
    ("skin", "Skin & Subcutaneous Tissue", 4, 6),
    ("msk", "Musculoskeletal System", 5, 7),
    ("cardiovascular", "Cardiovascular System", 9, 11),
    ("respiratory", "Respiratory System", 8, 10),
    ("gastrointestinal", "Gastrointestinal System", 6, 8),
    ("renal_male_repro", "Renal/Urinary & Male Reproductive Systems", 4, 6),
    ("pregnancy_female_repro", "Pregnancy/Childbirth & Female Reproductive System & Breast", 7, 9),
    ("endocrine", "Endocrine System", 5, 7),
    ("biostat_epi", "Biostatistics & Epidemiology/Population Health & Interpretation of the Medical Literature", 11, 13),
    ("social_sciences", "Social Sciences: Communication Skills/Ethics/Patient Safety", 7, 9),
]
STEP3_COMPETENCIES = [
    ("medical_knowledge", "Medical Knowledge: Applying Foundational Science Concepts (Day 1)", 11, 12, None),
    ("pc_diagnosis", "Patient Care: Diagnosis", 33, 36, None),
    ("pc_diagnosis.history_pe", "History/Physical Exam (Day 1)", 5, 9, "pc_diagnosis"),
    ("pc_diagnosis.lab_diagnostic", "Laboratory/Diagnostic Studies (Day 1)", 9, 12, "pc_diagnosis"),
    ("pc_diagnosis.diagnosis", "Diagnosis (Day 1)", 6, 10, "pc_diagnosis"),
    ("pc_diagnosis.prognosis", "Prognosis/Outcome (Day 2)", 8, 11, "pc_diagnosis"),
    ("pc_management", "Patient Care: Management (Day 2)", 32, 35, None),
    ("pc_management.health_maintenance", "Health Maintenance/Disease Prevention", 6, 11, "pc_management"),
    ("pc_management.pharmacotherapy", "Pharmacotherapy", 9, 13, "pc_management"),
    ("pc_management.clinical_interventions", "Clinical Interventions", 5, 9, "pc_management"),
    ("pc_management.mixed_management", "Mixed Management", 6, 11, "pc_management"),
    ("pbli", "Practice-based Learning & Improvement (Day 1)", 11, 13, None),
    ("communication_professionalism_sbp", "Communication/Professionalism/Systems-based Practice & Patient Safety (Day 1) / Communication & Professionalism (Day 2)", 7, 9, None),
]
EXAMS = [("step1", "USMLE Step 1"), ("step2ck", "USMLE Step 2 CK"), ("step3", "USMLE Step 3")]

# ─────────────────────────── Domains and cross-walk ───────────────────────────
DOMAIN_TO_SYSTEM = {
    #             step1                         step2ck                     step3
    "dev":       ("human_development",      "human_development",   "human_development"),
    "heme":      ("blood_immune",           "blood_immune",        "immune_blood_multisystem"),
    "immune":    ("blood_immune",           "blood_immune",        "immune_blood_multisystem"),
    "neuro":     ("behavioral_nervous",     "nervous",             "nervous"),
    "psych":     ("behavioral_nervous",     "behavioral_health",   "behavioral_health"),
    "msk":       ("msk_skin",               "msk_skin",            "msk"),
    "derm":      ("msk_skin",               "msk_skin",            "skin"),
    "cardio":    ("cardiovascular",         "cardiovascular",      "cardiovascular"),
    "resp":      ("respiratory_renal",      "respiratory",         "respiratory"),
    "renal":     ("respiratory_renal",      "renal_reproductive",  "renal_male_repro"),
    "gi":        ("gastrointestinal",       "gastrointestinal",    "gastrointestinal"),
    "endo":      ("reproductive_endocrine", "endocrine",           "endocrine"),
    "repro":     ("reproductive_endocrine", "renal_reproductive",  "pregnancy_female_repro"),
    "male":      ("reproductive_endocrine", "renal_reproductive",  "renal_male_repro"),
    "preg":      ("reproductive_endocrine", "pregnancy",           "pregnancy_female_repro"),
    "multi":     ("multisystem",            "multisystem",         "immune_blood_multisystem"),
    "nutrition": ("multisystem",            "multisystem",         "immune_blood_multisystem"),
    "biostat":   ("biostat_epi",            "biostat_epi",         "biostat_epi"),
    "social":    ("social_sciences",        "social_sciences",     "social_sciences"),
}
EXAM_INDEX = {"step1": 0, "step2ck": 1, "step3": 2}

ALL3 = ["step1", "step2ck", "step3"]
CLIN = ["step2ck", "step3"]

# (domain, slug, label, exams, keyword patterns). Each pattern is a regex matched
# against normalised lower-case text with a leading word boundary.
TOPICS = [
    # ── Human development ──
    ("dev", "milestones", "Developmental milestones", ALL3, ["developmental milestone", "milestone", "social smile", "babbl", "pincer grasp", "gross motor", "fine motor", "developmental delay", "speech delay"]),
    ("dev", "growth_puberty", "Growth & puberty", ALL3, ["puberty", "tanner", "growth chart", "short stature", "precocious", "delayed puberty", "growth velocity", "bone age"]),
    ("dev", "neonatal", "Normal newborn & neonatal care", ALL3, ["newborn", "neonat", "apgar", "preterm infant", "neonatal jaundice", "hyperbilirubinemia", "\\d+-day-old", "\\d+-hour-old"]),
    ("dev", "adolescent", "Adolescent health", ALL3, ["adolescen", "teenager", "heads(s)?s? assessment"]),
    ("dev", "aging", "Normal aging & geriatrics", ALL3, ["normal aging", "age-related", "geriatric", "elderly", "presbyopia", "presbycusis", "falls? in older", "frailty"]),
    ("dev", "well_care", "Care of the well patient & preventive care", ALL3, ["well-child", "well child", "health maintenance", "uspstf", "routine (health )?(examination|visit|check)", "annual (examination|visit)", "immunization schedule", "catch-up", "preventive care", "anticipatory guidance", "cancer screening", "lung cancer screening", "colorectal cancer screening"]),
    # ── Blood & immune ──
    ("heme", "anemia", "Anemias (microcytic, macrocytic, normocytic)", ALL3, ["anemia", "iron deficiency", "ferritin", "thalassemia", "vitamin b12", "b12 deficiency", "folate deficiency", "sideroblastic", "lead poisoning", "macrocytic", "microcytic", "anemia of chronic", "pernicious", "reticulocyte"]),
    ("heme", "hemolysis_hemoglobinopathy", "Hemolysis & hemoglobinopathies", ALL3, ["hemoly", "sickle", "g6pd", "spherocyt", "paroxysmal nocturnal", "pnh\\b", "coombs", "schistocyt", "hemoglobinopath", "hydroxyurea"]),
    ("heme", "hemostasis", "Hemostasis, bleeding & thrombotic disorders", ALL3, ["bleeding disorder", "hemophilia", "von willebrand", "thrombocytopen", "\\bitp\\b", "\\bttp\\b", "\\bhus\\b", "\\bdic\\b", "disseminated intravascular", "\\bhit\\b", "heparin-induced", "coagulat", "factor v leiden", "thrombophilia", "antiphospholipid", "petechia", "thrombotic microangiopathy", "platelet disorder"]),
    ("heme", "anticoagulation_vte", "VTE & anticoagulant/antiplatelet therapy", ALL3, ["warfarin", "heparin", "enoxaparin", "apixaban", "rivaroxaban", "dabigatran", "anticoagul", "deep venous thrombosis", "deep vein thrombosis", "\\bdvt\\b", "venous thromboembolism", "\\bvte\\b", "clopidogrel", "antiplatelet", "inr\\b"]),
    ("heme", "heme_malignancy", "Leukemias, lymphomas & plasma cell disorders", ALL3, ["leukemia", "lymphoma", "\\bcml\\b", "\\baml\\b", "\\bapl\\b", "\\bcll\\b", "hodgkin", "myeloma", "plasma cell", "myeloproliferative", "polycythemia", "burkitt", "essential thrombocythemia", "myelofibrosis", "bcr-abl", "imatinib", "monoclonal gammopathy"]),
    ("heme", "transfusion", "Blood products & transfusion medicine", ALL3, ["transfusion", "blood product", "packed red", "fresh frozen plasma", "\\bffp\\b", "cryoprecipitate", "platelet transfusion"]),
    ("heme", "heme_onc_pharm", "Heme/Onc pharmacology & oncologic emergencies", ALL3, ["chemotherap", "cisplatin", "doxorubicin", "cyclophosphamide", "methotrexate", "vincristine", "bleomycin", "tumor lysis", "oncologic emergenc", "neutropenic fever", "febrile neutropenia", "spinal cord compression", "superior vena cava syndrome", "rasburicase", "trastuzumab", "rituximab"]),
    ("immune", "immune_basics", "Innate & adaptive immunity, complement, cytokines", ALL3, ["complement", "cytokine", "interleukin", "\\bmhc\\b", "t-cell activation", "t cell", "b cell", "antibod", "immunoglobulin", "neutrophil function", "macrophage", "antigen present", "\\bil-\\d"]),
    ("immune", "hypersensitivity", "Hypersensitivity & allergy", ALL3, ["hypersensitivity", "anaphyla", "serum sickness", "angioedema", "allerg", "urticaria"]),
    ("immune", "immunodeficiency", "Immunodeficiencies", ALL3, ["immunodeficien", "\\bscid\\b", "bruton", "digeorge", "22q11", "wiskott", "chronic granulomatous", "ataxia-telangiectasia", "hyper-igm", "leukocyte adhesion", "\\bcvid\\b", "phagocyte defect", "chediak", "recurrent (bacterial )?infections"]),
    ("immune", "transplant_immunosuppression", "Transplantation & immunosuppressants", ALL3, ["transplant", "graft", "rejection", "tacrolimus", "cyclosporine", "mycophenolate", "sirolimus", "belatacept", "immunosuppress"]),
    ("immune", "vaccines", "Vaccines & immunization", ALL3, ["vaccin", "immunization", "immunize", "toxoid", "live attenuated", "\\bacip\\b"]),
    # ── Nervous system / special senses ──
    ("neuro", "stroke_hemorrhage", "Stroke & intracranial hemorrhage", ALL3, ["stroke", "ischemic attack", "\\btia\\b", "infarct of the", "subarachnoid", "intracerebral hemorrhage", "epidural hematoma", "subdural", "intracranial hemorrhage", "alteplase", "thrombectomy", "lacunar", "middle cerebral", "posterior cerebral", "cerebral artery"]),
    ("neuro", "localization", "Neuroanatomy & lesion localization (cord, brainstem, cranial nerves)", ALL3, ["spinal cord lesion", "brown-s", "syringomyelia", "brainstem", "cranial nerve", "oculomotor", "aphasia", "visual field", "hemianopia", "internuclear", "medial longitudinal", "lateral medullary", "wallenberg", "dorsal column", "corticospinal", "anterior spinal artery", "cerebellar", "neuroanatomy", "spinal cord"]),
    ("neuro", "seizures", "Seizures & epilepsy", ALL3, ["seizure", "epilep", "status epilepticus", "antiepileptic", "anticonvulsant", "valproate", "phenytoin", "carbamazepine", "levetiracetam", "lamotrigine"]),
    ("neuro", "headache", "Headache & facial pain", ALL3, ["headache", "migraine", "cluster headache", "trigeminal neuralgia", "triptan", "idiopathic intracranial", "pseudotumor"]),
    ("neuro", "neurodegenerative_demyelinating", "Neurodegenerative, movement & demyelinating disorders", ALL3, ["multiple sclerosis", "demyelinat", "parkinson", "huntington", "alzheimer", "dementia", "amyotrophic", "\\bals\\b", "levodopa", "tremor", "lewy bod", "frontotemporal", "normal pressure hydrocephalus", "wernicke"]),
    ("neuro", "neuromuscular", "Neuromuscular junction & peripheral nerve", ALL3, ["myasthenia", "guillain", "lambert-eaton", "neuromuscular junction", "botulism", "peripheral neuropathy", "polyneuropathy", "acetylcholine receptor", "pyridostigmine"]),
    ("neuro", "tumors_icp_coma", "CNS tumors, raised ICP, coma & brain death", ALL3, ["brain death", "death by neurologic", "coma\\b", "delirium", "altered mental status", "glasgow coma", "glioblastoma", "medulloblastoma", "meningioma", "astrocytoma", "ependymoma", "craniopharyngioma", "brain tumor", "hydrocephalus", "intracranial pressure", "herniation", "neurocutaneous", "neurofibromatosis", "tuberous sclerosis", "sturge"]),
    ("neuro", "special_senses", "Eye, ear & special senses", ALL3, ["glaucoma", "retina", "macular", "cataract", "uveitis", "conjunctivitis", "visual loss", "vision loss", "hearing loss", "vertigo", "otitis", "tinnitus", "papilledema", "pupil", "optic neuritis", "meniere"]),
    ("psych", "mood", "Depressive & bipolar disorders", ALL3, ["depress", "bipolar", "mania", "manic", "lithium", "ssri", "sertraline", "fluoxetine", "antidepressant", "mood stabilizer", "electroconvulsive", "postpartum depression", "dysthymi", "persistent depressive"]),
    ("psych", "psychosis", "Psychotic disorders & antipsychotics", ALL3, ["schizophren", "psychotic", "psychosis", "antipsychotic", "haloperidol", "clozapine", "risperidone", "olanzapine", "extrapyramidal", "tardive", "neuroleptic malignant", "dopamine pathway", "delusion", "hallucinat"]),
    ("psych", "anxiety_trauma_ocd", "Anxiety, trauma & obsessive-compulsive disorders", ALL3, ["anxiety", "panic", "phobia", "ptsd", "post-traumatic stress", "obsessive", "compulsi", "generalized anxiety", "benzodiazepine"]),
    ("psych", "substance_use", "Substance use, intoxication & withdrawal", ALL3, ["substance use", "alcohol use disorder", "withdrawal", "intoxication", "opioid use", "naloxone", "buprenorphine", "methadone", "naltrexone", "cocaine", "heroin", "delirium tremens", "cannabis", "alcoholic"]),
    ("psych", "personality_somatic_defense", "Personality, somatic disorders & defense mechanisms", ALL3, ["personality disorder", "borderline", "antisocial", "narcissistic", "defense mechanism", "somatic symptom", "conversion disorder", "factitious", "malingering", "illness anxiety", "sublimation", "displacement", "projection", "reaction formation"]),
    ("psych", "child_eating", "Child psychiatry & eating disorders", ALL3, ["adhd", "attention-deficit", "autism", "tourette", "oppositional defiant", "conduct disorder", "anorexia nervosa", "bulimia", "binge eating", "eating disorder", "refeeding", "methylphenidate"]),
    ("psych", "sleep_suicide", "Sleep disorders, suicide risk & involuntary holds", ALL3, ["sleep", "insomnia", "narcolepsy", "suicid", "involuntary hold", "involuntary admission", "self-harm"]),
    # ── MSK & skin ──
    ("msk", "limb_anatomy_nerve_injury", "Limb anatomy, brachial plexus & nerve injuries", ALL3, ["brachial plexus", "nerve injur", "median nerve", "ulnar nerve", "radial nerve", "axillary nerve", "musculocutaneous", "common fibular", "peroneal nerve", "femoral nerve", "obturator", "tibial nerve", "wrist drop", "erb", "klumpke", "rotator cuff", "carpal tunnel"]),
    ("msk", "arthritis_crystal", "Arthritis & crystal arthropathies", ALL3, ["gout", "pseudogout", "rheumatoid arthritis", "osteoarthritis", "septic arthritis", "arthritis", "allopurinol", "febuxostat", "colchicine", "urate", "monosodium", "calcium pyrophosphate"]),
    ("msk", "autoimmune_ctd", "Systemic lupus & connective tissue diseases", ALL3, ["lupus", "\\bsle\\b", "scleroderma", "systemic sclerosis", "sjogren", "sjögren", "dermatomyositis", "polymyositis", "inflammatory myopathy", "mixed connective", "polymyalgia", "anti-dsdna", "spondyl", "ankylosing", "reactive arthritis", "psoriatic arthritis"]),
    ("msk", "bone", "Bone disorders & bone tumors", ALL3, ["osteoporosis", "osteomalacia", "paget disease of bone", "osteogenesis imperfecta", "osteosarcoma", "ewing", "giant cell tumor", "osteopetrosis", "bisphosphonate", "dexa", "bone mineral density", "osteomyelitis", "avascular necrosis"]),
    ("msk", "orthopedics_trauma", "Orthopedics, fractures & sports injuries", ALL3, ["fracture", "dislocation", "compartment syndrome", "slipped capital", "legg-calv", "developmental dysplasia of the hip", "scaphoid", "anterior cruciate", "meniscus", "low back pain", "cauda equina", "sciatica", "ottawa", "orthoped"]),
    ("msk", "muscle_disorders", "Muscle disorders & muscular dystrophies", ALL3, ["muscular dystrophy", "duchenne", "becker", "myotonic", "rhabdomyolysis", "myopathy", "dystrophin"]),
    ("msk", "msk_pharm", "MSK & anti-inflammatory pharmacology", ALL3, ["nsaid", "dmard", "biologic", "tnf-alpha inhibitor", "tnf inhibitor", "infliximab", "adalimumab", "etanercept", "hydroxychloroquine", "glucocorticoid", "aspirin", "acetaminophen"]),
    ("derm", "blistering", "Blistering & immunobullous diseases", ALL3, ["pemphigus", "pemphigoid", "dermatitis herpetiformis", "bullae", "bullous", "blister"]),
    ("derm", "skin_cancer", "Skin cancer & pigmented lesions", ALL3, ["melanoma", "basal cell carcinoma", "squamous cell carcinoma of the skin", "actinic keratosis", "seborrheic keratosis", "nevus", "nevi", "xeroderma"]),
    ("derm", "inflammatory_dermatoses", "Inflammatory dermatoses", ALL3, ["psoriasis", "eczema", "atopic dermatitis", "contact dermatitis", "acne", "rosacea", "lichen planus", "seborrheic dermatitis", "erythema nodosum", "pityriasis"]),
    ("derm", "skin_infections_drug_eruptions", "Skin infections & drug eruptions", ALL3, ["cellulitis", "impetigo", "abscess", "tinea", "scabies", "erysipelas", "necrotizing fasciitis", "stevens-johnson", "toxic epidermal", "\\bdress\\b", "drug eruption", "skin and soft tissue"]),
    ("derm", "burns_wounds", "Burns & wound healing", ALL3, ["burn", "wound healing", "pressure ulcer", "pressure injur", "keloid", "parkland"]),
    # ── Cardiovascular ──
    ("cardio", "embryology_congenital", "Cardiac embryology & congenital heart disease", ALL3, ["congenital heart", "tetralogy", "transposition", "ventricular septal defect", "atrial septal defect", "\\bvsd\\b", "\\basd\\b", "patent ductus", "coarctation", "truncus", "eisenmenger", "cardiac embryolog", "heart tube", "endocardial cushion", "left-to-right shunt", "right-to-left shunt", "intracardiac shunt", "shunts\\b"]),
    ("cardio", "physiology_hemodynamics", "Cardiac physiology & hemodynamics", ALL3, ["pressure-volume", "pv loop", "cardiac output", "stroke volume", "ejection fraction", "preload", "afterload", "frank-starling", "starling", "baroreceptor", "cardiac cycle", "jugular venous", "wiggers", "mean arterial", "cardiovascular physiology", "cardiac physiology", "coronary circulation", "cardiovascular reflex"]),
    ("cardio", "ischemic_heart_disease", "Ischemic heart disease & acute coronary syndromes", ALL3, ["myocardial infarction", "\\bstemi\\b", "nstemi", "acute coronary", "angina", "troponin", "coronary artery disease", "chronic coronary", "st-segment elevation", "st elevation", "percutaneous coronary", "\\bpci\\b", "chest pain"]),
    ("cardio", "heart_failure_cardiomyopathy", "Heart failure & cardiomyopathies", ALL3, ["heart failure", "cardiomyopathy", "hypertrophic", "\\bhcm\\b", "reduced ejection", "\\bhfref\\b", "\\bhfpef\\b", "sacubitril", "dilated cardiomyopathy", "restrictive cardiomyopathy", "pulmonary edema"]),
    ("cardio", "arrhythmia", "Arrhythmias & conduction", ALL3, ["arrhythmi", "atrial fibrillation", "\\baf\\b", "afib", "atrial flutter", "ventricular tachycardia", "ventricular fibrillation", "torsades", "long qt", "qt prolong", "heart block", "av block", "bradycardia", "supraventricular", "wolff", "\\bsvt\\b", "antiarrhythmic", "amiodarone", "digoxin", "cardioversion", "pacemaker", "\\becg\\b"]),
    ("cardio", "valvular", "Valvular heart disease & murmurs", ALL3, ["murmur", "valv", "aortic stenosis", "mitral stenosis", "mitral regurgitation", "aortic regurgitation", "mitral valve prolapse", "rheumatic", "tavr", "maneuver"]),
    ("cardio", "hypertension", "Hypertension", ALL3, ["hypertension", "antihypertensive", "blood pressure goal", "renovascular", "hypertensive emergency", "hypertensive urgency", "ace inhibitor", "angiotensin receptor"]),
    ("cardio", "lipids_atherosclerosis", "Lipids & atherosclerosis", ALL3, ["statin", "ldl", "cholesterol", "hyperlipid", "dyslipid", "lipid", "atherosclero", "familial hypercholesterolemia", "ezetimibe", "pcsk9", "fibrate", "triglyceride"]),
    ("cardio", "endocarditis_pericardial", "Endocarditis, myocarditis & pericardial disease", ALL3, ["endocarditis", "pericarditis", "pericardial", "tamponade", "myocarditis", "duke criteria", "constrictive"]),
    ("cardio", "vascular_aorta", "Aorta, PAD & vascular disease", ALL3, ["aortic dissection", "aneurysm", "peripheral artery", "peripheral arterial", "claudication", "carotid", "limb ischemia", "ankle-brachial", "\\baaa\\b", "mesenteric ischemia", "varicose", "vascular surgery"]),
    ("cardio", "vasculitis", "Vasculitides", ALL3, ["vasculitis", "kawasaki", "giant cell arteritis", "temporal arteritis", "takayasu", "polyarteritis", "granulomatosis with polyangiitis", "anca", "henoch", "iga vasculitis", "buerger"]),
    ("cardio", "shock_syncope", "Shock & syncope", ALL3, ["shock", "syncope", "vasopressor", "norepinephrine", "hypotension", "cardiogenic", "orthostatic"]),
    ("cardio", "cardio_pharm", "Cardiovascular pharmacology", ALL3, ["beta-blocker", "beta blocker", "metoprolol", "carvedilol", "calcium channel blocker", "nitrate", "nitroglycerin", "hydralazine", "cardiovascular pharmacology", "cardiac pharmacology", "lisinopril", "losartan", "spironolactone"]),
    # ── Respiratory ──
    ("resp", "physiology", "Respiratory physiology (V/Q, A-a gradient, O2-Hb curve, PFTs)", ALL3, ["v/q", "ventilation-perfusion", "a-a gradient", "alveolar-arterial", "oxygen-hemoglobin", "o 2 -hb", "o2-hb", "dissociation curve", "compliance", "pulmonary function", "\\bpfts?\\b", "fev1", "dlco", "high altitude", "dead space", "respiratory physiology"]),
    ("resp", "obstructive", "Obstructive lung disease (asthma, COPD, bronchiectasis, CF)", ALL3, ["asthma", "copd", "chronic obstructive", "emphysema", "chronic bronchitis", "bronchiectasis", "cystic fibrosis", "alpha 1-antitrypsin", "alpha-1 antitrypsin", "a1at", "alpha 1 -antitrypsin", "inhaled corticosteroid", "albuterol", "tiotropium", "\\blaba\\b", "\\blama\\b"]),
    ("resp", "restrictive_ild", "Restrictive & interstitial lung disease, sarcoidosis, pneumoconioses", ALL3, ["interstitial lung", "pulmonary fibrosis", "\\bipf\\b", "sarcoid", "pneumoconios", "asbestos", "silicosis", "berylliosis", "coal worker", "hypersensitivity pneumonitis", "restrictive"]),
    ("resp", "pulmonary_vascular", "Pulmonary embolism & pulmonary hypertension", ALL3, ["pulmonary embol", "\\bpe\\b", "pulmonary hypertension", "cor pulmonale", "wells score", "ct pulmonary angiograph", "d-dimer"]),
    ("resp", "lung_cancer_nodules", "Lung nodules & lung cancer", ALL3, ["lung cancer", "lung nodule", "pulmonary nodule", "small cell", "adenocarcinoma of the lung", "squamous cell carcinoma of the lung", "bronchogenic", "fleischner", "pancoast", "paraneoplastic", "mesothelioma"]),
    ("resp", "pneumonia_infection", "Pneumonia & respiratory infection", ALL3, ["pneumonia", "bronchiolitis", "croup", "epiglottitis", "pertussis", "influenza", "tuberculosis", "\\btb\\b", "lung abscess", "community-acquired", "aspiration"]),
    ("resp", "pleural", "Pleural disease & pneumothorax", ALL3, ["pleural effusion", "pleural", "pneumothorax", "empyema", "light criteria", "thoracentesis", "chest tube", "hemothorax"]),
    ("resp", "critical_care_ventilation", "ARDS, respiratory failure, ventilation & sleep apnea", ALL3, ["ards", "acute respiratory distress", "respiratory distress syndrome", "nrds", "mechanical ventilation", "ventilator", "intubat", "respiratory failure", "obstructive sleep apnea", "hypoxemi", "\\bpeep\\b", "surfactant"]),
    ("renal", "physiology", "Renal physiology (GFR, clearance, tubular transport)", ALL3, ["gfr", "glomerular filtration", "clearance", "renal plasma flow", "\\brpf\\b", "filtration fraction", "proximal tubule", "loop of henle", "collecting duct", "renal physiology", "tubular", "aldosterone action", "renin"]),
    ("renal", "acid_base_electrolytes", "Acid-base, sodium & potassium disorders", ALL3, ["acid-base", "acidosis", "alkalosis", "anion gap", "renal tubular acidosis", "\\brta\\b", "hyponatremia", "hypernatremia", "hyperkalemia", "hypokalemia", "siadh", "diabetes insipidus", "potassium", "sodium disorder", "bicarbonate", "winter"]),
    ("renal", "glomerular", "Glomerular disease (nephrotic & nephritic)", ALL3, ["nephrotic", "nephritic", "glomerulo", "minimal change", "membranous", "fsgs", "focal segmental", "iga nephropathy", "post-streptococcal", "poststreptococcal", "alport", "goodpasture", "anti-gbm", "proteinuria", "hematuria"]),
    ("renal", "aki_ckd", "Acute kidney injury & chronic kidney disease", ALL3, ["acute kidney injury", "\\baki\\b", "acute tubular necrosis", "\\batn\\b", "chronic kidney disease", "\\bckd\\b", "dialysis", "kdigo", "prerenal", "creatinine", "uremi", "contrast nephropathy", "interstitial nephritis"]),
    ("renal", "stones_urinary_tract", "Nephrolithiasis, UTI & lower urinary tract", ALL3, ["nephrolithiasis", "kidney stone", "renal colic", "urolithiasis", "urinary tract infection", "\\buti\\b", "pyelonephritis", "cystitis", "incontinence", "urinary retention", "hydronephrosis", "vesicoureteral"]),
    ("renal", "cystic_tumors", "Cystic kidney disease & renal tumors", ALL3, ["polycystic kidney", "renal cell carcinoma", "wilms", "cystic kidney", "bladder cancer", "urothelial", "renal mass", "potter", "horseshoe kidney", "inherited tubulopath", "bartter", "gitelman", "liddle"]),
    ("renal", "renal_pharm", "Diuretics & renal pharmacology", ALL3, ["diuretic", "furosemide", "thiazide", "hydrochlorothiazide", "acetazolamide", "amiloride", "mannitol", "loop diuretic", "renal pharm"]),
    # ── GI ──
    ("gi", "embryology_anatomy", "GI embryology & anatomy", ALL3, ["gi embryolog", "midgut", "foregut", "hindgut", "malrotation", "meckel", "omphalocele", "gastroschisis", "tracheoesophageal", "duodenal atresia", "hirschsprung", "celiac trunk", "superior mesenteric", "gi anatomy"]),
    ("gi", "physiology_hormones", "GI physiology, hormones & bilirubin", ALL3, ["gastrin", "secretin", "cholecystokinin", "\\bcck\\b", "somatostatin", "gi hormone", "bilirubin", "gilbert", "crigler", "dubin", "bile acid", "gi physiology"]),
    ("gi", "esophagus_stomach", "Esophagus, stomach & peptic ulcer disease", ALL3, ["dysphagia", "esophag", "achalasia", "gerd", "gastroesophageal reflux", "barrett", "peptic ulcer", "gastric ulcer", "duodenal ulcer", "h\\. pylori", "helicobacter", "zollinger", "gastrinoma", "gastric cancer", "pyloric stenosis"]),
    ("gi", "malabsorption_ibd", "Malabsorption & inflammatory bowel disease", ALL3, ["celiac", "malabsorption", "crohn", "ulcerative colitis", "inflammatory bowel", "\\bibd\\b", "lactose intolerance", "whipple", "tropical sprue", "irritable bowel", "mesalamine"]),
    ("gi", "liver", "Liver disease & hepatitis", ALL3, ["cirrhosis", "hepatitis", "hepatic", "liver", "hemochromatosis", "wilson", "ascites", "varices", "hepatic encephalopathy", "spontaneous bacterial peritonitis", "acetaminophen (toxicity|overdose|hepatotoxicity)", "hepatocellular", "alcoholic liver", "transaminase", "\\balt\\b", "\\bast\\b"]),
    ("gi", "biliary_pancreas", "Biliary tract & pancreas", ALL3, ["cholecyst", "cholangitis", "choledocholithiasis", "gallstone", "biliary", "pancreatitis", "pancreatic", "primary sclerosing", "primary biliary", "ercp", "lipase"]),
    ("gi", "gi_bleeding", "GI bleeding", ALL3, ["gi bleed", "gastrointestinal bleed", "hematemesis", "melena", "hematochezia", "upper gastrointestinal bleeding", "lower gastrointestinal bleeding", "variceal"]),
    ("gi", "colorectal", "Colon: polyps, colorectal cancer & diverticular disease", ALL3, ["colorectal", "colon cancer", "colonoscopy", "polyp", "adenoma", "lynch", "familial adenomatous", "diverticul", "anal fissure", "hemorrhoid", "colitis"]),
    ("gi", "acute_abdomen", "Acute abdomen, bowel obstruction & hernias", ALL3, ["appendicitis", "bowel obstruction", "small bowel obstruction", "volvulus", "intussusception", "hernia", "acute abdomen", "perforat", "peritonitis", "ileus", "laparotomy"]),
    ("gi", "gi_pharm", "GI pharmacology", ALL3, ["proton pump inhibitor", "omeprazole", "ondansetron", "antiemetic", "metoclopramide", "h2 blocker", "laxative", "loperamide", "misoprostol", "sucralfate", "gi pharmacology"]),
    # ── Endocrine / reproductive / pregnancy ──
    ("endo", "diabetes", "Diabetes mellitus, DKA/HHS & hypoglycemia", ALL3, ["diabetes mellitus", "diabetic", "type 1 diabetes", "type 2 diabetes", "\\bdka\\b", "ketoacidosis", "hyperosmolar", "insulin", "metformin", "hba1c", "hemoglobin a1c", "hypoglycemi", "sglt2", "glp-1", "sulfonylurea", "insulinoma"]),
    ("endo", "thyroid", "Thyroid disorders", ALL3, ["thyroid", "hypothyroid", "hyperthyroid", "graves", "hashimoto", "thyroiditis", "levothyroxine", "methimazole", "propylthiouracil", "\\btsh\\b", "goiter", "thyroid storm"]),
    ("endo", "adrenal", "Adrenal disorders (Cushing, aldosteronism, CAH, insufficiency, pheo)", ALL3, ["adrenal", "cushing", "aldosteronism", "hyperaldosteronism", "conn syndrome", "congenital adrenal hyperplasia", "21-hydroxylase", "11β-hydroxylase", "addison", "pheochromocytoma", "cortisol", "steroid synthesis", "\\bcah\\b", "hpa axis"]),
    ("endo", "pituitary", "Pituitary & hypothalamus", ALL3, ["pituitary", "prolactin", "acromegaly", "growth hormone", "hypopituitar", "sheehan", "posterior pituitary", "vasopressin", "\\badh\\b", "hypothalam"]),
    ("endo", "calcium_parathyroid", "Calcium, parathyroid & vitamin D", ALL3, ["hypercalcemi", "hypocalcemi", "parathyroid", "\\bpth\\b", "hyperparathyroid", "vitamin d", "calcitriol", "calcium"]),
    ("endo", "men_neuroendocrine", "MEN syndromes & neuroendocrine tumors", ALL3, ["\\bmen\\b", "men1", "men2", "men 1", "men 2", "multiple endocrine neoplasia", "carcinoid", "neuroendocrine tumor", "medullary thyroid", "vipoma", "glucagonoma"]),
    ("repro", "menstrual_amenorrhea_pcos", "Menstrual cycle, amenorrhea, AUB, PCOS & disorders of sex development", ALL3, ["sex development", "androgen insensitivity", "5α-reductase", "5-alpha-reductase", "mullerian", "müllerian", "wolffian", "turner", "hypospadias", "reproductive embryolog", "ambiguous genitalia", "menstrual cycle", "amenorrhea", "abnormal uterine bleeding", "palm-coein", "polycystic ovary", "\\bpcos\\b", "hyperandrogen", "hirsutism", "menorrhagia", "dysmenorrhea", "endometriosis", "fibroid", "leiomyoma"]),
    ("repro", "contraception_infertility_menopause", "Contraception, infertility & menopause", ALL3, ["contracepti", "intrauterine device", "\\biud\\b", "infertility", "menopaus", "hormone therapy", "ovulation induction", "clomiphene", "letrozole", "oral contraceptive", "emergency contraception"]),
    ("repro", "gyn_oncology", "Gynecologic oncology & cervical screening", ALL3, ["cervical cancer", "cervical", "pap\\b", "\\bhpv\\b", "endometrial", "ovarian", "adnexal", "colposcopy", "\\bcin\\b", "ca-125", "vulvar"]),
    ("repro", "breast", "Breast disease", ALL3, ["breast", "mammogra", "brca", "fibroadenoma", "mastitis", "tamoxifen", "aromatase inhibitor"]),
    ("repro", "vaginitis_pid_sti", "Vaginitis, PID & sexually transmitted infections", ALL3, ["vaginitis", "vaginosis", "trichomon", "candidiasis", "pelvic inflammatory", "\\bpid\\b", "gonorrh", "chlamydia", "syphilis", "sexually transmitted", "\\bsti\\b", "genital herpes", "urethritis"]),
    ("male", "male_repro", "Male reproductive system (testis, prostate, ED)", ALL3, ["testicular", "testis", "testes", "prostate", "\\bbph\\b", "benign prostatic", "erectile dysfunction", "pde5", "sildenafil", "klinefelter", "cryptorchidism", "varicocele", "torsion", "scrot", "epididym", "male reproductive", "hypogonadism"]),
    ("preg", "prenatal_physiology", "Prenatal care, pregnancy physiology & screening", ALL3, ["prenatal", "pregnancy physiology", "trimester", "gestation", "weeks' gestation", "weeks gestation", "first-trimester screening", "cell-free dna", "quad screen", "pregnant", "pregnancy"]),
    ("preg", "pregnancy_complications", "Pregnancy complications (HTN, GDM, ectopic, bleeding, GTD, preterm)", ALL3, ["preeclampsia", "eclampsia", "hellp", "gestational diabetes", "gestational hypertension", "ectopic", "placenta previa", "placental abruption", "abruptio", "molar pregnancy", "hydatidiform", "gestational trophoblastic", "choriocarcinoma", "preterm labor", "pprom", "rupture of membranes", "miscarriage", "pregnancy loss", "spontaneous abortion", "teratogen"]),
    ("preg", "labor_postpartum", "Labor, delivery & the puerperium", ALL3, ["labor", "delivery", "fetal heart rate", "decelerations", "cesarean", "postpartum", "postpartum hemorrhage", "uterine atony", "rh(d)?", "rhogam", "anti-d", "alloimmunization", "lactation", "breastfeeding", "puerper", "shoulder dystocia", "oxytocin"]),
    # ── Multisystem / general principles ──
    ("multi", "bacteria", "Bacteriology", ALL3, ["bacteri", "gram-positive", "gram-negative", "gram stain", "staphylococc", "streptococc", "clostridi", "c\\. difficile", "neisseria", "escherichia", "e\\. coli", "salmonella", "shigella", "listeria", "pseudomonas", "spirochete", "lyme", "rickettsia", "mycobacteri", "anaerob", "toxin", "superantigen", "enterococc"]),
    ("multi", "viruses_hiv", "Virology & HIV", ALL3, ["virus", "viral", "\\bhiv\\b", "antiretroviral", "\\bart\\b", "herpes", "cytomegalovirus", "\\bcmv\\b", "epstein-barr", "\\bebv\\b", "varicella", "zoster", "influenza", "hepatitis b", "hepatitis c", "antiviral", "acyclovir", "oseltamivir", "opportunistic"]),
    ("multi", "fungi_parasites", "Mycology & parasitology", ALL3, ["fung", "candida", "aspergill", "cryptococc", "histoplasm", "blastomyc", "coccidioid", "pneumocystis", "mycos", "malaria", "plasmodium", "parasit", "helminth", "nematode", "cestode", "tapeworm", "cysticercosis", "giardia", "amebi", "toxoplasm"]),
    ("multi", "antimicrobials", "Antimicrobial pharmacology & resistance", ALL3, ["antibiotic", "antibacterial", "antimicrobial", "penicillin", "cephalosporin", "vancomycin", "aminoglycoside", "fluoroquinolone", "macrolide", "carbapenem", "resistance", "isoniazid", "rifampin", "antifungal", "amphotericin", "azole"]),
    ("multi", "sepsis_systemic_infection", "Sepsis & systems-based infectious disease", ALL3, ["sepsis", "septic", "meningitis", "encephalitis", "fever of unknown", "bacteremia", "infectious disease", "endemic", "travel"]),
    ("multi", "neoplasia", "Neoplasia, oncogenes & paraneoplastic syndromes", ALL3, ["neoplas", "oncogene", "tumor suppressor", "\\bp53\\b", "\\brb\\b", "\\bras\\b", "carcinogen", "metasta", "paraneoplastic", "tumor marker", "cancer genetics"]),
    ("multi", "cell_injury_inflammation", "Cell injury, inflammation, repair & amyloidosis", ALL3, ["cell injury", "necrosis", "apoptosis", "inflammation", "inflammatory mediator", "leukocyte extravasation", "wound healing", "granuloma", "amyloid", "hypertrophy", "metaplasia", "free radical"]),
    ("multi", "genetics", "Medical genetics & inheritance", ALL3, ["inheritance", "autosomal", "x-linked", "mitochondrial inheritance", "imprinting", "trinucleotide", "anticipation", "hardy-weinberg", "chromosom", "trisomy", "down syndrome", "fragile x", "penetrance", "mosaicism", "genetic", "karyotype", "microdeletion"]),
    ("multi", "metabolism_inborn_errors", "Metabolism & inborn errors", ALL3, ["glycogen storage", "lysosomal storage", "urea cycle", "ornithine", "phenylketonuria", "maple syrup", "galactosemia", "fructose", "fatty acid oxidation", "glycolysis", "pyruvate", "gluconeogenesis", "inborn error", "metabolism", "gaucher", "fabry", "tay-sachs", "pompe", "homocystinuria", "lipoprotein", "hmp shunt", "pentose phosphate"]),
    ("multi", "molecular_cell_biology", "Molecular & cell biology, lab techniques", ALL3, ["dna repair", "dna replication", "transcription", "translation", "rna processing", "splic", "protein trafficking", "collagen", "cell cycle", "pcr\\b", "western blot", "elisa", "laboratory technique", "cytoskelet", "golgi", "i-cell"]),
    ("nutrition", "vitamins_minerals", "Vitamins, minerals & nutrition", ALL3, ["vitamin", "thiamine", "niacin", "riboflavin", "pyridoxine", "cobalamin", "folate", "scurvy", "beriberi", "pellagra", "zinc", "copper", "selenium", "malnutrition", "kwashiorkor", "marasmus", "obesity", "diet", "nutrition", "refeeding", "enteral", "parenteral", "trace element", "micronutrient", "bmi"]),
    ("multi", "pharm_principles", "Pharmacokinetics, pharmacodynamics & autonomic pharmacology", ALL3, ["pharmacokinetic", "pharmacodynamic", "half-life", "steady state", "loading dose", "maintenance dose", "volume of distribution", "zero-order", "first-order", "bioavailability", "therapeutic index", "partial agonist", "antagonis", "efficacy", "potency", "autonomic", "cholinergic", "anticholinergic", "adrenergic", "muscarinic", "cyp450", "cyp3a4", "p450", "pharmacogenomic", "drug interaction", "enzyme kinetics"]),
    ("multi", "toxicology_environmental", "Toxicology, antidotes & environmental emergencies", ALL3, ["toxicolog", "poisoning", "overdose", "antidote", "toxidrome", "carbon monoxide", "cyanide", "organophosphate", "salicylate", "methanol", "ethylene glycol", "heat stroke", "hypothermia", "drowning", "snake", "envenomation", "lithium toxicity", "acetaminophen overdose"]),
    ("multi", "trauma_surgery_periop", "Trauma, perioperative & critical care principles", CLIN, ["trauma", "\\batls\\b", "primary survey", "fast exam", "\\bfast\\b", "gunshot", "stab wound", "motor vehicle", "preoperative", "postoperative", "perioperative", "anesthe", "malignant hyperthermia", "airway", "resuscitation", "chest trauma", "head trauma"]),
    # ── Biostatistics / epi ──
    ("biostat", "diagnostic_tests", "Diagnostic test evaluation (Sn, Sp, PPV, NPV, LR, cutoffs)", ALL3, ["sensitivity", "specificity", "predictive value", "\\bppv\\b", "\\bnpv\\b", "likelihood ratio", "cutoff", "roc curve", "false positive", "false negative", "true positive"]),
    ("biostat", "risk_measures", "Measures of frequency & risk (incidence, prevalence, RR, OR, NNT)", ALL3, ["incidence", "prevalence", "relative risk", "odds ratio", "attributable risk", "absolute risk", "number needed", "\\bnnt\\b", "\\bnnh\\b", "\\barr\\b", "\\brrr\\b", "relative risk reduction", "hazard ratio", "measure of association"]),
    ("biostat", "study_design", "Study design", ALL3, ["study design", "cohort", "case-control", "cross-sectional", "randomized", "clinical trial", "meta-analysis", "case series", "crossover", "ecological study", "phase (i|ii|iii|iv|1|2|3|4) trial"]),
    ("biostat", "bias_confounding", "Bias & confounding", ALL3, ["bias", "confound", "effect modification", "lead-time", "length-time", "selection bias", "recall bias", "hawthorne", "intention-to-treat", "survival benefit", "attrition"]),
    ("biostat", "hypothesis_testing", "Hypothesis testing, power & confidence intervals", ALL3, ["hypothesis", "p value", "p-value", "type i error", "type ii error", "statistical power", "power of", "confidence interval", "chi-square", "t-test", "anova", "normal distribution", "standard deviation", "statistical test", "correlation coefficient", "null"]),
    ("biostat", "prevention_screening", "Prevention levels & screening programs", ALL3, ["primary prevention", "secondary prevention", "tertiary prevention", "quaternary prevention", "levels of prevention", "screening program", "screening bias"]),
    ("biostat", "ebm_literature", "Evidence-based medicine & interpreting the literature", ALL3, ["evidence-based", "interpretation of the (study|results|literature)", "drug advertisement", "abstract", "forest plot", "kaplan-meier", "biostatistics", "epidemiology"]),
    # ── Social sciences / ethics / safety ──
    ("social", "capacity_consent", "Decision-making capacity & informed consent", ALL3, ["decision-making capacity", "capacity", "informed consent", "informed refusal", "refuses", "autonomy", "competen"]),
    ("social", "confidentiality_reporting", "Confidentiality, HIPAA & mandatory reporting", ALL3, ["confidential", "hipaa", "mandatory report", "duty to warn", "tarasoff", "reportable", "child abuse", "elder abuse", "intimate partner", "domestic violence", "nonaccidental", "non-accidental"]),
    ("social", "minors", "Minors, parental consent & emancipation", ALL3, ["minor", "emancipat", "parental consent", "parental refusal", "parents refuse", "jehovah"]),
    ("social", "end_of_life_surrogates", "Advance directives, surrogates & end-of-life care", ALL3, ["advance directive", "surrogate", "health care proxy", "durable power", "do-not-resuscitate", "\\bdnr\\b", "palliative", "hospice", "withdraw(al|ing) of (care|life)", "end-of-life", "physician-assisted"]),
    ("social", "communication", "Communication skills (bad news, interpreters, difficult conversations)", ALL3, ["breaking bad news", "bad news", "interpreter", "communication", "empath", "open-ended", "motivational interviewing", "angry patient", "difficult patient", "shared decision"]),
    ("social", "patient_safety_qi", "Patient safety, medical error & quality improvement", ALL3, ["patient safety", "medical error", "disclos", "root cause", "quality improvement", "pdsa", "near miss", "sentinel event", "checklist", "handoff", "swiss cheese", "adverse event", "sbar"]),
    ("social", "professionalism", "Professionalism, gifts, conflicts of interest & impaired colleagues", ALL3, ["professionalism", "gift", "conflict of interest", "impaired (physician|colleague)", "romantic", "boundar", "colleague", "industry"]),
    ("social", "legal_health_systems", "Law & health systems (EMTALA, malpractice, insurance)", ALL3, ["emtala", "malpractice", "medicare", "medicaid", "insurance", "negligence", "law\\b", "legal", "good samaritan", "subpoena", "court order"]),
]

TOPIC_IDS = [f"topic.{d}.{s}" for d, s, *_ in TOPICS]
assert len(set(TOPIC_IDS)) == len(TOPIC_IDS), "duplicate topic id"

# Free-text tag words that point at a domain (bonus to that domain's topics).
TAG_DOMAIN_HINTS = [
    (r"respirat|pulmon|\blung|pneumo", "resp"), (r"renal|nephro|kidney|urinar", "renal"),
    (r"cardi|heart|coronary|vascular", "cardio"), (r"\bgi\b|gastro|hepat|liver|bowel|pancrea|biliar", "gi"),
    (r"endocrin|thyroid|adrenal|diabet|pituitar", "endo"), (r"neuro|brain|spinal|stroke", "neuro"),
    (r"psych|behavior", "psych"), (r"hemat|heme\b|heme/onc|anemi", "heme"), (r"immun", "immune"),
    (r"dermat|skin", "derm"), (r"\bmsk\b|rheumat|orthop", "msk"), (r"obstet|pregnan", "preg"),
    (r"gynec|reproductive", "repro"), (r"biostat|epidemiol", "biostat"),
    (r"ethic|communication|patient safety|quality improvement", "social"), (r"micro|infect", "multi"),
]
TAG_HINT_BONUS = 3.0
PRIOR_BONUS = 0.75
SECONDARY_RATIO = 0.7

# Chapter priors: domains a chapter is expected to cover (tie-break + fallback).
CHAPTER_PRIOR = {
    "ch01": ["multi", "immune", "nutrition"],
    "ch02": ["multi"],
    "ch03": ["multi", "biostat", "social"],
    "ch04": ["heme", "multi"],
    "ch05": ["cardio", "resp", "renal"],
    "ch06": ["gi", "endo", "repro", "preg", "male"],
    "ch07": ["neuro", "psych", "msk", "derm"],
    "ch08": ["cardio", "resp", "renal"],
    "ch09": ["gi", "endo", "heme", "multi", "msk"],
    "ch10": ["multi", "gi", "cardio", "msk", "derm"],
    "ch11": ["dev", "preg", "repro"],
    "ch12": ["psych", "neuro", "social", "biostat"],
    "mock1": [],
    "mock2": [],
}
CHAPTER_FALLBACK = {  # used only if nothing matched at all
    "ch01": "multi", "ch02": "multi", "ch03": "multi", "ch04": "heme", "ch05": "cardio",
    "ch06": "gi", "ch07": "neuro", "ch08": "cardio", "ch09": "multi", "ch10": "multi",
    "ch11": "dev", "ch12": "psych", "mock1": "multi", "mock2": "multi",
}

# ─────────────────────────── Text helpers ───────────────────────────
_TAG = re.compile(r"<[^>]+>")


def norm(text: str | None) -> str:
    t = html.unescape(_TAG.sub(" ", text or ""))
    t = t.replace("–", "-").replace("—", "-").replace("α", "alpha ").replace("β", "beta ")
    t = t.replace("’", "'")
    return re.sub(r"\s+", " ", t).strip().lower()


def _compile(pat: str) -> re.Pattern:
    p = pat.lower().replace("α", "alpha ").replace("β", "beta ")
    if not p.startswith("\\b"):
        p = r"\b" + p
    return re.compile(p)


COMPILED = [(f"topic.{d}.{s}", d, [_compile(k) for k in kws]) for d, s, _l, _e, kws in TOPICS]


def score_topics(fields: list[tuple[str, float]]) -> dict[str, tuple[float, list[str]]]:
    """fields = [(normalised_text, weight)]. Returns {topic_id: (score, matched_keywords)}.

    A keyword counts once per field (presence, not frequency) so long explanations
    do not swamp the tags."""
    out: dict[str, tuple[float, list[str]]] = {}
    for tid, _dom, pats in COMPILED:
        score, hits = 0.0, []
        for text, w in fields:
            if not text:
                continue
            for p in pats:
                if p.search(text):
                    score += w
                    hits.append(p.pattern.replace("\\b", ""))
        if score:
            out[tid] = (score, sorted(set(hits)))
    return out


def domain_of(topic_id: str) -> str:
    return topic_id.split(".")[1]


def system_id(exam: str, domain: str) -> str:
    return f"{exam}.sys.{DOMAIN_TO_SYSTEM[domain][EXAM_INDEX[exam]]}"


# ─────────────────────────── Taxonomy emission ───────────────────────────
def build_taxonomy() -> list[dict]:
    rows: list[dict] = []

    def add(id_, kind, parent, label, exams, lo=None, hi=None, sort=0):
        rows.append({"id": id_, "kind": kind, "parent_id": parent, "label": label, "exam": exams,
                     "weight_min": lo, "weight_max": hi, "sort": sort})

    for i, (eid, lab) in enumerate(EXAMS, 1):
        add(eid, "exam", None, lab, [eid], None, None, i)

    def systems(exam, table):
        for i, (s, lab, lo, hi) in enumerate(table, 1):
            add(f"{exam}.sys.{s}", "system", exam, lab, [exam], lo, hi, i)

    def comps(exam, table):
        for i, (c, lab, lo, hi, parent) in enumerate(table, 1):
            add(f"{exam}.comp.{c}", "competency", f"{exam}.comp.{parent}" if parent else exam, lab, [exam], lo, hi, i)

    systems("step1", STEP1_SYSTEMS)
    comps("step1", STEP1_COMPETENCIES)
    for i, (d, lab, lo, hi) in enumerate(STEP1_DISCIPLINES, 1):
        add(f"step1.disc.{d}", "discipline", "step1", lab, ["step1"], lo, hi, i)
    systems("step2ck", STEP2CK_SYSTEMS)
    comps("step2ck", STEP2CK_COMPETENCIES)
    for i, (c, lab, lo, hi) in enumerate(STEP2CK_CLINICAL, 1):
        add(f"step2ck.clin.{c}", "clinical_science", "step2ck", lab, ["step2ck"], lo, hi, i)
    systems("step3", STEP3_SYSTEMS)
    comps("step3", STEP3_COMPETENCIES)

    counters: dict[str, int] = collections.Counter()
    for d, s, lab, exams, _kws in TOPICS:
        parent = f"step1.sys.{DOMAIN_TO_SYSTEM[d][0]}"
        if "step1" not in exams:  # clinical-only topic: parent under Step 2 CK system
            parent = system_id("step2ck", d)
        counters[parent] += 1
        add(f"topic.{d}.{s}", "topic", parent, lab, list(exams), None, None, counters[parent])
    return rows


# ─────────────────────────── Question mapping ───────────────────────────
AGE = re.compile(r"\b(\d+)-(year|month|week|day|hour)-old\b")
PEDS_WORDS = re.compile(r"\b(newborn|neonate|infant|toddler|child|boy|girl|adolescent)\b")
PREG_WORDS = re.compile(r"\b(is|currently|who is) pregnant|\bweeks' gestation|\bat \d+ weeks|\bweeks pregnant|\bpostpartum|\bgravida|\bg\dp\d|\bprenatal visit|\bin labor")
SURG_WORDS = re.compile(r"\b(trauma|surgery|surgical|laparotomy|laparoscop|operative|appendectomy|cholecystectomy|postoperative|preoperative|incision|fracture|gunshot|stab wound|motor vehicle|burns?|hernia|anesthe|amputation|exploration|repair)")
PHARM_LEAD = re.compile(r"\b(pharmacotherapy|drug|medication|agent|mechanism of action|adverse effect|side effect|treatment|therapy|antidote|regimen|dose)")


def lead_in(stem_norm: str) -> str:
    sents = re.split(r"(?<=[.?])\s+", stem_norm)
    q = [s for s in sents if s.endswith("?")]
    return q[-1] if q else (sents[-1] if sents else "")


def disciplines_step1(tags: str, lead: str, full: str, domains: set[str]) -> list[str]:
    d: list[str] = []
    T = tags + " " + lead

    def has(rx, text=T):
        return re.search(rx, text) is not None

    if has(r"pharm|drug|antidote|toxicity|toxicolog|antibiotic|antibacterial|antiviral|antifungal|antiepileptic|antipsychotic|diuretic|dmard|biologics|immunosuppressant|chemotherapy") or PHARM_LEAD.search(lead):
        d.append("pharmacology")
    if has(r"micro|bacteri|viral|virus|fung|myco|parasit|infect|organism|pathogen|toxins|gram-|spirochet|rickettsia|zoonos|hepatitis|hiv|herpes|malaria|cestod|nematod|c\. difficile|salmonella|neisseria|clostrid|endocarditis"):
        d.append("microbiology")
    if has(r"immun|antibod|complement|hypersensitivity|transplant|vaccine|cytokine|t-cell|phagocyte|rejection"):
        d.append("immunology")
    if has(r"biochem|metabol|enzyme|vitamin|glycogen|urea cycle|lysosomal|dna repair|transcription|rna|collagen|glycolysis|pyruvate|fatty acid|lipoprotein|amino acid|heme synthesis|hmp|g6pd|porphyri|laboratory technique|protein trafficking|thiamine|deficiency of which"):
        d.append("biochemistry")
    if has(r"genetic|inheritance|mutation|chromosom|trisomy|imprinting|trinucleotide|hardy|klinefelter|marfan|muscular dystrophy|mitochondrial|pharmacogenomic|cancer genetics|oncogene|tumor suppressor|neurocutaneous|men syndrome|men1|men2"):
        d.append("genetics")
    if has(r"physiolog|pv loop|clearance|gfr|v/q|a-a gradient|o 2 -hb|o2-hb|acid-base|hemodynamic|compliance|altitude|shunt|coronary circulation|reflex|sleep physiology|menstrual cycle|pregnancy physiology|posterior pituitary|hpa axis|thyroid function|calcium|potassium") or re.search(r"\b(increase|decrease|change)s?\b.*\b(most likely|expected)\b", lead):
        d.append("physiology")
    if has(r"anatom|embryolog|nerve injur|plexus|spinal cord vascular|brainstem|cranial nerve|derived from|embryologic|visual pathway|neuroanatomy|developmental") :
        d.append("anatomy_embryology")
    if has(r"histolog|cell biology|electron micro|cytoskelet|organelle|epitheli|biopsy specimen"):
        d.append("histology_cell_biology")
    if has(r"ethic|communicat|biostat|epidemiol|psychiatr|behavio|defense mechanism|sleep|substance|somatic|psychotic|patient safety|quality improvement|bad news|confidential|consent|capacity|surrogate|advance directive|interpreter|study design|nnt|predictive|incidence|prevalence|screening|hypothesis|power|confidence"):
        d.append("behavioral_sciences")
    if has(r"vitamin|nutrition|diet|malnutrition|thiamine|niacin|folate|b12|trace element|obesity|refeeding|collagen / vitamins|zinc|copper|iron metabolism|dietary", T + " " + full[:1500]):
        d.append("nutrition")
    # pathology: disease mechanism / diagnosis / morphology, or nothing else matched
    if has(r"pathology|patholog|diagnos|cause|finding|biopsy|lesion|tumor|neoplas|carcinoma|syndrome|disease|disorder|injury|necrosis|inflamm|anemia|leukemia|lymphoma|nephr|glomerul") or not d or domains & {"heme", "cardio", "resp", "renal", "gi", "endo", "neuro", "msk", "derm", "repro", "male"} and "physiology" not in d:
        d.append("pathology")
    order = [x[0] for x in STEP1_DISCIPLINES]
    return [f"step1.disc.{x}" for x in order if x in set(d)]


def clinical_step2(chapter: str, stem: str, tags: str, domains: set[str], section_hint: str) -> list[str]:
    c: set[str] = set()
    m = AGE.search(stem)
    peds = False
    if m:
        n, unit = int(m.group(1)), m.group(2)
        peds = unit != "year" or n < 18
    elif PEDS_WORDS.search(stem[:200]):
        peds = True
    if peds or re.search(r"pediatric|neonat|croup|kawasaki|intussusception|developmental milestones|child abuse", tags):
        c.add("pediatrics")
    child = m is not None and (m.group(2) != "year" or int(m.group(1)) < 12)
    if (PREG_WORDS.search(stem) and not child) or domains & {"preg", "repro"} or re.search(r"obstetric|gynecolog|pregnan|contracept|pcos", tags):
        c.add("obgyn")
    if "psych" in domains or re.search(r"psychiatr|lithium|antipsychotic|substance|suicid|eating disorder", tags):
        c.add("psychiatry")
    if chapter == "ch10" or re.search(r"surgery|trauma|burn|carotid stenosis|limb ischemia|pediatric surgery|chest trauma|hernia|appendicitis", tags) or (SURG_WORDS.search(stem) and "trauma_surgery_periop" in section_hint):
        c.add("surgery")
    # Medicine: adult non-surgical/non-psych content, plus neurology, ethics, biostat
    adult = m is not None and m.group(2) == "year" and int(m.group(1)) >= 18
    if chapter in ("ch08", "ch09") or (adult and not (c & {"surgery", "psychiatry", "obgyn"})) or not c or domains & {"neuro", "social", "biostat"} and chapter != "ch11":
        c.add("medicine")
    order = [x[0] for x in STEP2CK_CLINICAL]
    return [f"step2ck.clin.{x}" for x in order if x in c]


def competency(exam: str, lead: str, domains: set[str], tags: str, top_topic: str | None) -> str:
    if exam == "step1":
        if "social" in domains and re.search(r"communicat|bad news|interpreter|ethic|consent|capacity|confidential|surrogate|minor|disclos|advance", tags + lead):
            return "step1.comp.communication"
        if "biostat" in domains or (top_topic or "").startswith("topic.social.patient_safety"):
            return "step1.comp.pbli"
        if re.search(r"most likely diagnosis|most likely cause|most likely (underlying )?condition", lead):
            return "step1.comp.pc_diagnosis.diagnosis"
        if re.search(r"(examination|exam|physical) (is )?most likely (to )?(show|reveal|find)|additional finding|further history", lead):
            return "step1.comp.pc_diagnosis.history_pe"
        return "step1.comp.medical_knowledge"
    # step2ck
    if "biostat" in domains and re.search(r"biostat|epidemiol|study|predictive|nnt|bias|sensitivity|specificity|power|odds|risk", tags + lead):
        return "step2ck.comp.pbli"
    if (top_topic or "").startswith("topic.social.patient_safety") or re.search(r"patient safety|quality improvement|root cause|medical error", tags):
        return "step2ck.comp.systems_based_practice"
    if "social" in domains and re.search(r"ethic|consent|capacity|confidential|surrogate|minor|disclos|advance|professional|communicat|interpreter|bad news|refus", tags + lead):
        return "step2ck.comp.professionalism"
    if re.search(r"most likely diagnosis|most likely cause|most likely (underlying )?(condition|explanation|mechanism)|best explains", lead):
        return "step2ck.comp.pc_diagnosis"
    if re.search(r"next (best )?step in (diagnosis|evaluation)|most appropriate (diagnostic|test|study|imaging|initial (test|study|diagnostic))|most likely to confirm|most accurate test|most useful", lead):
        return "step2ck.comp.pc_lab_diagnostic"
    if re.search(r"pharmacotherapy|medication|drug|antibiotic|regimen", lead):
        return "step2ck.comp.pc_pharmacotherapy"
    if re.search(r"screen|vaccin|immuniz|prevent|counsel|recommendation", lead):
        return "step2ck.comp.pc_health_maintenance"
    if re.search(r"prognosis|most likely outcome|most likely (to )?develop|greatest risk|increased risk|complication", lead):
        return "step2ck.comp.pc_prognosis"
    if re.search(r"intervention|procedure|surgery|operative", lead):
        return "step2ck.comp.pc_clinical_interventions"
    return "step2ck.comp.pc_mixed_management"


def map_question(q: dict, explain: bool = False) -> tuple[list[str], dict]:
    exam = q["exam"]
    chapter = q.get("chapterId") or ""
    raw_tags = [t for t in q.get("tags", []) if not re.match(r"^(step \d|step 1/2 ck|step 2 ck|difficulty:)", t.strip().lower())]
    tags = norm(" | ".join(raw_tags))
    stem = norm(q.get("stemHtml"))
    opts = norm(" ".join(o.get("html", "") for o in q.get("options", [])))
    expl = norm((q.get("answerHtml") or "") + " " + (q.get("explanationHtml") or ""))
    lead = lead_in(stem)

    scores = score_topics([(tags, 3.0), (stem, 1.0), (opts, 0.5), (expl, 0.5)])
    prior = set(CHAPTER_PRIOR.get(chapter, []))
    hinted = {d for rx, d in TAG_DOMAIN_HINTS if re.search(rx, tags)}
    m_age = AGE.search(stem)
    child = bool(m_age) and (m_age.group(2) != "year" or int(m_age.group(1)) < 12)
    if child:  # a child's vignette may mention the mother's pregnancy; never code it as Pregnancy
        scores = {k: v for k, v in scores.items() if domain_of(k) != "preg"}

    def adj(tid: str) -> float:
        d = domain_of(tid)
        return scores[tid][0] + (PRIOR_BONUS if d in prior else 0.0) + (TAG_HINT_BONUS if d in hinted else 0.0)

    ranked = sorted(scores.items(), key=lambda kv: (-adj(kv[0]), kv[0]))
    used_fallback = False
    topics: list[str] = []
    if ranked:
        top = adj(ranked[0][0])
        for tid, _v in ranked:
            if adj(tid) >= max(1.5, SECONDARY_RATIO * top) and len(topics) < 3:
                topics.append(tid)
        if not topics:
            topics.append(ranked[0][0])
    domains_ordered: list[str] = []
    for t in topics:
        d = domain_of(t)
        if d not in domains_ordered:
            domains_ordered.append(d)
    if not domains_ordered:
        domains_ordered = [CHAPTER_FALLBACK.get(chapter, "multi")]
        used_fallback = True

    # primary system = domain of best topic; nutrition never stands alone if another domain exists
    systems: list[str] = []
    non_nutr = [d for d in domains_ordered if d != "nutrition"] or domains_ordered
    for d in non_nutr[:2]:
        sid = system_id(exam, d)
        if sid not in systems:
            systems.append(sid)
    nutrition_flag = "nutrition" in domains_ordered or re.search(r"\b(vitamin|nutrition|diet|malnutrition|refeeding)", tags) is not None
    if exam == "step2ck" and nutrition_flag and "step2ck.sys.nutrition" not in systems:
        systems.append("step2ck.sys.nutrition")

    dset = set(domains_ordered)
    ids = list(systems)
    if exam == "step1":
        ids += disciplines_step1(tags, lead, stem + " " + expl, dset)
    elif exam == "step2ck":
        ids += clinical_step2(chapter, stem, tags, dset, " ".join(topics))
    ids.append(competency(exam, lead, dset, tags, topics[0] if topics else None))
    ids += topics
    info = {"fallback": used_fallback, "scores": {k: (round(adj(k), 2), v[1]) for k, v in ranked[:6]}, "lead": lead}
    return ids, info


# ─────────────────────────── CLI ───────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--emit-taxonomy", action="store_true", help="write data/taxonomy.json and exit")
    ap.add_argument("--questions", default=str(DATA / "questions.json"))
    ap.add_argument("--taxonomy", default=str(DATA / "taxonomy.json"))
    ap.add_argument("--out", default=str(DATA / "question_taxonomy_map.json"))
    ap.add_argument("--explain", help="print scoring details for one question id")
    a = ap.parse_args(argv)

    if a.emit_taxonomy:
        rows = build_taxonomy()
        pathlib.Path(a.taxonomy).write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n")
        kinds = collections.Counter(r["kind"] for r in rows)
        print(f"wrote {a.taxonomy}: {len(rows)} nodes {dict(kinds)}")
        return 0

    tax = {r["id"]: r for r in json.loads(pathlib.Path(a.taxonomy).read_text())}
    qs = json.loads(pathlib.Path(a.questions).read_text())

    if a.explain:
        q = next(x for x in qs if x["id"] == a.explain)
        ids, info = map_question(q, explain=True)
        print(json.dumps({"id": q["id"], "tags": q.get("tags"), "lead": info["lead"], "ids": ids,
                          "fallback": info["fallback"], "top_scores": info["scores"]}, indent=1, ensure_ascii=False))
        return 0

    out: dict[str, list[str]] = {}
    fallback = 0
    for q in qs:
        ids, info = map_question(q)
        missing = [i for i in ids if i not in tax]
        if missing:
            raise SystemExit(f"{q['id']}: unknown taxonomy ids {missing}")
        out[q["id"]] = ids
        fallback += info["fallback"]
    pathlib.Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")

    # ── coverage report ──
    n = len(qs)
    by_exam = collections.Counter(q["exam"] for q in qs)
    kind_of = lambda i: tax[i]["kind"]
    has = lambda q, k: any(kind_of(i) == k for i in out[q["id"]])
    print(f"wrote {a.out}: {len(out)} questions")
    print(f"system coverage: {sum(has(q, 'system') for q in qs)}/{n}")
    s1 = [q for q in qs if q["exam"] == "step1"]
    s2 = [q for q in qs if q["exam"] == "step2ck"]
    print(f"step1 discipline coverage: {sum(has(q, 'discipline') for q in s1)}/{len(s1)}")
    print(f"step2ck clinical-science coverage: {sum(has(q, 'clinical_science') for q in s2)}/{len(s2)}")
    print(f"competency coverage: {sum(has(q, 'competency') for q in qs)}/{n}")
    print(f"topic coverage: {sum(has(q, 'topic') for q in qs)}/{n}; chapter-fallback used: {fallback}")
    used_topics = {i for v in out.values() for i in v if kind_of(i) == 'topic'}
    print(f"distinct topics used: {len(used_topics)}/{sum(1 for r in tax.values() if r['kind']=='topic')}")
    for exam in ("step1", "step2ck"):
        print(f"\n{exam} primary-system distribution vs official range (n={by_exam[exam]}):")
        cnt = collections.Counter()
        for q in qs:
            if q["exam"] == exam:
                prim = next(i for i in out[q["id"]] if kind_of(i) == "system" and not i.endswith(".sys.nutrition"))
                cnt[prim] += 1
        for sid, r in sorted(((k, v) for k, v in tax.items() if v["kind"] == "system" and exam in v["exam"]), key=lambda kv: kv[1]["sort"]):
            pct = 100 * cnt[sid] / by_exam[exam]
            rng = f"{r['weight_min']}-{r['weight_max']}%" if r["weight_min"] is not None else "n/a"
            print(f"  {sid:42s} {cnt[sid]:4d}  {pct:5.1f}%  official {rng}")
        kinds = ("discipline",) if exam == "step1" else ("clinical_science",)
        cc = collections.Counter(i for q in qs if q["exam"] == exam for i in out[q["id"]] if kind_of(i) in kinds)
        print(f"  {kinds[0]} (multi-label):")
        for k, v in sorted(cc.items(), key=lambda kv: -kv[1]):
            r = tax[k]
            print(f"    {k:40s} {v:4d}  {100*v/by_exam[exam]:5.1f}%  official {r['weight_min']}-{r['weight_max']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
