from urls import U, L

LV = "2026-11-15"


def links(*pairs):
    return [{"label": lbl, "url": U[k]} for lbl, k in pairs]


# (key, file_slug, title, summary_md, official_links)
STAGES = [
    ("eligibility", "eligibility", "Eligibility and ECFMG application",
     f"Before any exam, confirm your medical school is listed in WDOMS with an ECFMG Sponsor Note that covers your graduation year; with no Sponsor Note you are not eligible {L('ECFMG_REQ','ECFMG')}. "
     f"Open a MyIntealth account ($110) and submit the Application for ECFMG Certification ($580) plus diploma and transcript verification ($110 each) {L('IMGPREP','IMGPrep')}. "
     f"Do this first: a 2027 Pathway application requires your ECFMG application to be “Accepted” or “Pending Credential Verification”, and your OET booking must carry your MyIntealth ID {L('PATHWAYS27','ECFMG Pathways')} {L('FAQ27','2027 FAQ')}. "
     f"Since January 2026 you register for USMLE through FSMB, not ECFMG {L('ECFMG_NEWS_PW','ECFMG News')}.",
     links(("ECFMG certification requirements (2026 IB)", "ECFMG_REQ"), ("WDOMS Sponsor Notes", "SPONSOR_NOTES"),
           ("ECFMG 2027 Pathways", "PATHWAYS27"), ("USMLE eligibility", "USMLE_ELIG"))),

    ("step1", "step1", "USMLE Step 1",
     f"Step 1 is pass/fail, now 14 × 30-minute blocks (280 items) {L('LECTURIO_2026','Lecturio')}, and costs $695 + $210 international surcharge when taken in India {L('IMGPREP','IMGPrep')}. "
     f"The 2025 first-time pass rate for IMGs was 75% {L('USMLE_PERF','USMLE')}. "
     f"You get at most 3 attempts at a Step in 12 months and 4 failed attempts end USMLE eligibility, so sit only when ready {L('USMLE_ELIG','USMLE')}. "
     f"A NEET PG year gives strong recall; the job is retraining for mechanism-based, two-step questions using UWorld and NBME self-assessments.",
     links(("USMLE eligibility and attempt limits", "USMLE_ELIG"), ("USMLE performance data", "USMLE_PERF"),
           ("NBME self-assessments", "NBME_SA"), ("UWorld Step 1", "UWORLD1"))),

    ("oet_pathway", "oet-pathway", "OET Medicine and ECFMG Pathway",
     f"ECFMG certification needs an accepted, unexpired Pathway, which includes OET Medicine at 350 in Listening, Reading and Speaking and 300 in Writing in one sitting {L('ECFMG_CS','ECFMG')} {L('FAQ_OLD','ECFMG FAQ')}. "
     f"No USMLE pass is needed to apply for a 2027 Pathway; passes are needed by the Rank Order List deadline of the Match you enter {L('FAQ27','2027 FAQ')}. "
     f"The 2027 Pathway deadline is 31 Jan 2027 (ET) and ECFMG must receive OET scores directly by then {L('PATHWAYS27','ECFMG')}. "
     f"ECFMG has not announced 2028 Pathways {L('ECFMG_NEWS_PW','ECFMG News')}, so a December 2026 OET plus a 2027 Pathway is low-regret insurance.",
     links(("ECFMG 2027 Pathways", "PATHWAYS27"), ("2027 Pathways FAQ", "FAQ27"), ("Pathway 1", "PW1"),
           ("Pathways 3-5", "PW35"), ("Pathway expiration", "PW_EXP"))),

    ("step2ck", "step2ck", "USMLE Step 2 CK",
     f"Step 2 CK is scored; the pass mark is 218 since 1 Jul 2025 and the mean is about 250 {L('IATROX_CK','iatroX')}. "
     f"This is the score programs filter on: 89% of programs use it for interview decisions {L('NRMP_PD26','NRMP 2026 PD Survey')}. "
     f"Matched non-US IMGs in Internal Medicine averaged 248 vs 242 for unmatched (2024) {L('NRMP_CO24','NRMP')}. "
     f"Budget about 12 weeks, sit only when predictors are in range, and remember a passed Step cannot be retaken for a higher score {L('USMLE_ELIG','USMLE')}.",
     links(("USMLE eligibility", "USMLE_ELIG"), ("UWorld Step 2 CK", "UWORLD2"), ("NBME self-assessments", "NBME_SA"),
           ("NRMP Charting Outcomes IMG 2024", "NRMP_CO24"))),

    ("usce", "usce", "US clinical exposure, letters and the B-1 visa",
     f"MBBS graduates can generally do only observerships (shadowing, no patient contact); hands-on clerkships on a B-1 are limited to students {L('AMA_OBS','AMA')} {L('TEMPLE','Temple ISSS')}. "
     f"The main value is the specialty-specific US letter, cited by 84–86% of program directors for interview selection {L('NRMP_PD24','NRMP 2024')} {L('NRMP_PD26','NRMP 2026')}. "
     f"B1/B2 interview waits in India were about 6–12 months in Sept 2026 {L('TRAVELMAN','TravelManToday')}, and some hosts require the B-1 before you apply {L('UTHSCSA','UTHSCSA')}, so the visa is the hidden critical path.",
     links(("AMA observership FAQ", "AMA_OBS"), ("State Dept visa wait times", "STATE_WAIT"),
           ("UAB observership", "UAB"), ("KUMC observership", "KUMC"))),

    ("specialty_choice", "specialty-choice", "Choosing specialties",
     f"In 2026, non-US IMGs needing visa sponsorship matched at 54.4%, a five-year low {L('NRMP_2026_PR','NRMP')}. "
     f"Categorical Internal Medicine is by far the largest destination (3,448 non-US IMGs, 30.8% of positions) and Family Medicine is the least score-driven safety {L('NRMP_2026_DATA','NRMP 2026')}. "
     f"Matched and unmatched IMGs differ by only 5–10 Step 2 CK points but matched IMGs ranked about 2.5× as many programs {L('NRMP_CO24','NRMP 2024')}, so interviews are the bottleneck.",
     links(("NRMP 2026 Results and Data", "NRMP_2026_DATA"), ("NRMP Charting Outcomes 2024", "NRMP_CO24"),
           ("NRMP Step 2 CK baseline report", "NRMP_STEP2_BASE"))),

    ("eras_application", "eras-application", "ERAS application",
     f"ERAS 2028 dates are not yet published; in the 2027 cycle MyERAS opened 4 Jun 2026, applicants could submit from 2 Sep and programs reviewed from 23 Sep {L('AAMC_ERAS_TL','AAMC')}. "
     f"Fees are $11 per program for the first 30 and $30 each after, per specialty {L('LECTURIO_ERAS','Lecturio')}. "
     f"Internal Medicine gives 3 gold and 12 silver signals {L('AAMC_SIGNAL','AAMC')}. Submit on day one with 100–150 IM and about 30 FM programs.",
     links(("AAMC ERAS timeline", "AAMC_ERAS_TL"), ("AAMC program signaling", "AAMC_SIGNAL"),
           ("NRMP applicant calendar", "NRMP_CAL"))),

    ("interviews_ranking", "interviews-ranking", "Interviews and rank order list",
     f"Interviews run roughly October to January {L('LECTURIO_ERAS','Lecturio')}. "
     f"Program directors' top ranking factors are interpersonal skills (89%) and interview interactions (87%) {L('NRMP_PD24','NRMP 2024 PD Survey')}. "
     f"Step 1, Step 2 CK and an accepted, unexpired Pathway must all be in place by the Rank Order List certification deadline {L('PATHWAYS27','ECFMG')} {L('PW_EXP','ECFMG')}; in the 2027 Match that deadline is 3 Mar 2027 {L('NRMP_CAL','NRMP')}.",
     links(("NRMP applicant calendar", "NRMP_CAL"), ("NRMP 2024 PD Survey", "NRMP_PD24"))),

    ("match_day", "match-day", "Match week",
     f"In the 2027 Match, Match status is released 15 Mar, SOAP runs 15–18 Mar and Match Day is 19 Mar {L('NRMP_CAL','NRMP')}; 2028 dates are not yet published. "
     f"The week after Match starts the visa critical path: contract, MoHFW Statement of Need, Intealth J-1 application, DS-2019 {L('EVSP_PRE','Intealth')} {L('CGISF','CGI San Francisco')}.",
     links(("NRMP applicant calendar", "NRMP_CAL"), ("Intealth pre-arrival", "EVSP_PRE"))),

    ("visa_j1", "visa-j1", "J-1 visa (and the H-1B branch)",
     f"J-1 is the default residency visa. It needs ECFMG/Intealth sponsorship and a Statement of Need from MoHFW naming the exact specialty, is generally capped at 7 years, and carries the INA 212(e) two-year home-residency rule {L('EVSP_GEN','ECFMG EVSP')}. "
     f"MoHFW will not issue a NORI “under any circumstance” {L('CGISF','CGI San Francisco')}, and each SoN needs a ₹10 lakh surety bond with two sureties {L('MOHFW_OM','MoHFW')}. "
     f"In 2026 some Indian matched residents faced refusals and long administrative processing {L('SAH','South Asian Herald')}, so start every step the week after Match. "
     f"H-1B for residency needs Step 3 before the petition and is exposed to the $100,000 fee litigation {L('FSMB_ATTY','FSMB/attorneys')}.",
     links(("ECFMG EVSP general requirements", "EVSP_GEN"), ("Intealth pre-arrival", "EVSP_PRE"),
           ("CGI San Francisco: Statement of Need", "CGISF"), ("MoHFW SoN guidelines", "MOHFW_OM"),
           ("SEVIS I-901", "ICE_I901"))),

    ("pre_departure", "pre-departure", "Pre-departure",
     f"Between visa issuance and the flight: buy J-1-compliant insurance starting on the DS-2019 start date {L('EVSP_PRE','Intealth')}, secure the state training license your program needs {L('TMB','TMB')} {L('RUSH','Rush GME')}, complete TB screening {L('CDC_TB','CDC')}, plan money transfers under LRS {L('ICICI_LRS','ICICI')}, and pack a hand-carry document folder {L('EVSP_TRAVEL','Intealth')}. "
     f"You may enter the US no earlier than 30 days before the DS-2019 start date {L('EVSP_PRE','Intealth')}.",
     links(("Intealth pre-arrival", "EVSP_PRE"), ("Intealth travel", "EVSP_TRAVEL"), ("RBI/LRS explainer (ICICI)", "ICICI_LRS"),
           ("CBP: bringing money", "CBP_MONEY"))),

    ("arrival_first_30_days", "arrival-first-30-days", "Arrival: first 30 days",
     f"At the airport check that your I-94 reads “J-1 D/S”, then report to the program so the Training Program Liaison validates your arrival in SEVIS {L('EVSP_PRE','Intealth')}. "
     f"Apply for an SSN (free) after waiting 11 business days from SEVIS validation {L('EVSP_PRE','Intealth')} {L('SSA_PUB','SSA')}. "
     f"Then: bank account and secured card {L('CAPONE','Capital One')}, driver license {L('CA_DMV_DL','CA DMV')}, nonresident tax setup {L('IRS_P519','IRS')}, and a J-2 EAD if your spouse will work ($520) {L('MIT_J2','MIT')}. "
     f"Watch the postponed DHS rule replacing D/S with fixed admission dates {L('UTHSCSA_DS','UTHSCSA OIS')}.",
     links(("Intealth pre-arrival", "EVSP_PRE"), ("SSA: SSN for non-citizens", "SSA_PUB"), ("IRS Publication 519", "IRS_P519"),
           ("CA DMV driver licenses", "CA_DMV_DL"))),

    ("residency_life", "residency-life", "Residency life",
     f"ACGME caps work at 80 hours a week averaged over 4 weeks, with one day in seven free {L('ACGME_2017','ACGME')}. "
     f"PGY-1 pay averaged $68,166 nationally and $77,649 in the West (AAMC, Jul 2025) {L('AMA_PAY','AMA')}; Bay Area programs pay $84k–$102k {L('UCSF_SAL','UCSF')} {L('FREIDA_HIGHLAND_BEN','FREIDA')}. "
     f"J-1 residents cannot moonlight {L('EVSP_GEN','ECFMG')}, and any trip abroad needs a travel-signed DS-2019 and a valid J-1 stamp {L('EVSP_TRAVEL','Intealth')}.",
     links(("ACGME Section VI", "ACGME_2017"), ("Intealth travel", "EVSP_TRAVEL"), ("Physician Support Line", "PSL"))),

    ("step3_and_licensure", "step3-and-licensure", "Step 3 and licensure",
     f"Step 3 requires a valid ECFMG certificate and is offered only in the US and territories {L('USMLE_ELIG','USMLE')} {L('CAREERS360','Careers360')}. "
     f"In California, IMG residents must hold a Postgraduate Training License within 180 days of starting and can get a full license after 24 months of approved training, with Step 3 required {L('MBC','MBC')} {L('UCDAVIS_GME','UC Davis GME')}. "
     f"California requires all parts of Step 3 within four attempts {L('CA_BP2177','B&P 2177')}. Take Step 3 in PGY-1 unless you are pursuing an H-1B for residency.",
     links(("USMLE eligibility", "USMLE_ELIG"), ("Medical Board of California", "MBC"), ("NYSED license requirements", "NYSED"))),

    ("post_residency", "post-residency", "After residency: fellowship, boards, waiver jobs, H-1B, green card",
     f"A J-1 physician must spend two years in India or obtain a waiver {L('EVSP_GEN','ECFMG')}; India will not issue a NORI {L('CGISF','CGI SF')}. "
     f"Most J-1 doctors take a 3-year Conrad 30 or HHS waiver job in a shortage area and switch to H-1B {L('USCIS_CONRAD','USCIS')}; the federal HHS waiver ran a months-long backlog in 2025–26 {L('KFF','KFF Health News')}. "
     f"The India green-card queue is long: EB-2 India was at 1 Nov 2013 in the Nov 2026 bulletin (verify on travel.state.gov) {L('F1JOBS','F1Jobs')}.",
     links(("USCIS Conrad 30", "USCIS_CONRAD"), ("HCAI California J-1 waiver", "HCAI"), ("AAMC ERAS Fellowship timeline", "AAMC_FELLOW"),
           ("ABIM eligibility", "ABIM"))),

    ("california_bay_area", "california-bay-area", "California and the Bay Area",
     f"Bay Area academic and Kaiser programs take almost no non-US IMGs (Kaiser NorCal Oakland and SF IM about 0%, Santa Clara Valley about 3%) {L('RMAI_CA','ResidencyMatch.ai')}. "
     f"Realistic California targets are Central Valley and Inland Empire programs, nearly all J-1 only; Highland in Oakland is a stretch (577 interviews for 30 seats) {L('FREIDA_HIGHLAND','FREIDA')}. "
     f"California's Conrad 30 requires shortage-area practice {L('HCAI','HCAI')}, so a Bay Area job is more realistic as the second job after a waiver, not the first.",
     links(("HCAI California J-1 waiver", "HCAI"), ("Medical Board of California", "MBC"), ("FREIDA Highland IM", "FREIDA_HIGHLAND"))),
]
