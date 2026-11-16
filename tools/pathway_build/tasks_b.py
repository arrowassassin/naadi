from urls import L

TASKS = {}

TASKS["usce"] = ("usce", [
    ("Understand what an MBBS graduate can do",
     f"International graduates can apply only for observerships, which are strictly shadowing with no hands-on patient contact {L('AMA_OBS','AMA')}. "
     f"The B-1 for unpaid hands-on clerkships is limited to medical students, and observers generally may not examine patients, touch the EHR or write notes {L('TEMPLE','Temple ISSS citing 9 FAM')}.",
     "start", 0, "AMA_OBS"),
    ("File a DS-160 and book the B1/B2 interview now",
     f"B1/B2 waits (State Dept data via a secondary source, 17 Sep 2026) were about 6 months in Kolkata, 7 in Chennai, 7.5 in New Delhi, 11 in Mumbai and 11.5 in Hyderabad {L('TRAVELMAN','TravelManToday')}; check the live page {L('STATE_WAIT','State Dept')}. "
     f"The B1/B2 fee is $185 {L('VISAVISA','visavisa.in')}. "
     f"You may book at any Indian post, because since 6 Sep 2025 you must interview in your country of nationality or residence {L('FAEGRE','Faegre Drinker')}.",
     "start", 185, "STATE_WAIT"),
    ("Plan for an in-person interview",
     f"From 2 Sep 2025 most applicants must interview in person; the dropbox waiver remains only for narrow B1/B2 renewals (prior visa expired within 12 months, same class, no refusals) {L('FAEGRE','Faegre Drinker')}. "
     f"A $250 Visa Integrity Fee was legislated in 2025 but no official notice of collection at Indian posts had been found as of 23 Oct 2026 (verify) {L('VISAVISA','visavisa.in')} {L('BOUNDLESS','Boundless')}.",
     "start+3d", 0, "FAEGRE"),
    ("Keep every visa application truthful and consistent",
     f"In 2026 some Indian physicians were refused visa appointments; Intealth briefed US agencies that B-visa observerships and J-1 residency are separate activities {L('AAIM_INTEALTH','AAIM')}. "
     f"Be ready at the port of entry to explain that the visit is an unpaid observership; B status allows a stay of up to 6 months {L('TEMPLE','Temple ISSS')}. "
     "Keep public social-media profiles consistent with your DS-160.",
     "start+3d", 0, "AAIM_INTEALTH"),
    ("Send the KU Medical Center inquiry before 1 Dec 2026",
     f"KUMC accepts IMGs regardless of immigration status (students are ineligible), charges a $50 inquiry fee per program and $3,000 a month, for up to 4 weeks {L('KUMC','KUMC')}. "
     f"Jan–Jun 2027 placements: apply 15 Oct–1 Dec 2026; Jul–Dec 2027 placements: apply 15 Jan–15 Mar 2027 {L('KUMC','KUMC')}.",
     "date:2026-12-01", 50, "KUMC"),
    ("Apply to UAB once Step 1 is passed",
     f"UAB requires an MBBS within the last 5 years and a passed Step 1; it charges a $350 non-refundable application fee plus $4,250 per 4 weeks once a host is found {L('UAB','UAB')}. "
     f"Apply 3 months before the start date; you stay in the candidate pool for up to 6 months, and UAB recommends a B-1/B-2 with a B-1 admission stamp {L('UAB','UAB')}.",
     "observership_start-90d", 4600, "UAB"),
    ("Apply to UT Health San Antonio Hospital Medicine (needs B-1 first)",
     f"Requires intent to apply for US residency, a passed Step 1 and an LOR or dean's letter; costs $50 + $450 processing + $500 a week (about $2,450 for 4 weeks) plus a $10 badge {L('UTHSCSA','UTHSCSA')}. "
     f"A B-1 is required before you apply and B-2 admission is not acceptable; apply at least 60 days ahead (current windows Feb–Apr 2027) {L('UTHSCSA','UTHSCSA')}.",
     "observership_start-60d", 2460, "UTHSCSA"),
    ("Consider Missouri Neurology only with real neurology interest",
     f"Requires Step 1 and Step 2 CK, an MU neurology sponsor and 3 LORs; $750 application + $1,800 per 4-week block, up to three blocks, no July starts {L('MU_NEURO','University of Missouri')}.",
     "observership_start-90d", 2550, "MU_NEURO"),
    ("SUNY Upstate Medicine: only after ECFMG certification",
     f"Requires first-attempt scores of 200 or more on Step 1 and Step 2 CK and a valid ECFMG certificate; $300 application + $1,500 per 2-week rotation, no July starts {L('UPSTATE','SUNY Upstate')}. "
     f"Submit a complete file 8 weeks before the start; a decision comes within 4 weeks {L('UPSTATE','SUNY Upstate')}.",
     "observership_start-56d", 1800, "UPSTATE"),
    ("Know the expensive and high-bar options",
     f"Johns Hopkins Hospital Medicine is built for IMGs: $150 application + $9,000 (Oct–Apr) or $10,000 (May–Sep) for 6 weeks, and observers may request a letter on completion {L('HOPKINS','Hopkins')}. "
     f"Mount Sinai Medical Center Miami Beach (not Mount Sinai New York) requires USMLE 220 or more on every Step taken and applications 90 days ahead {L('MSMC_MIAMI','MSMC')}.",
     "observership_start-90d", None, "HOPKINS"),
    ("Skip programs that will not help your Match",
     f"UCLA Health charges $750, requires a B-1, and is “unable to provide any recommendation letters” {L('UCLA_OBS','UCLA')}. "
     f"Cleveland Clinic will not consider applicants who intend to apply for US residency and does not refund fees {L('CCF','Cleveland Clinic')}. "
     f"Keck USC requires current employment by a foreign health organization {L('USC_OBS','USC')}.",
     "start+7d", 0, "UCLA_OBS"),
    ("Ask about the letter policy in writing before paying",
     f"Specialty-specific letters are cited by 84% of program directors for interview selection {L('NRMP_PD24','NRMP 2024')}. "
     f"Hosts differ: Hopkins writes letters on request {L('HOPKINS','Hopkins')}, UCLA writes none {L('UCLA_OBS','UCLA')}. "
     "Choose 4-week blocks with one consistent attending, because 2-week rotations rarely produce a detailed letter.",
     "observership_start-90d", 0, "NRMP_PD24"),
    ("Check program-specific USCE rules for your target list",
     f"UCSF-Fresno IM requires IMGs without US rotations to have at least 4 weeks of observership in at least 2 clinical settings, plus 3 LORs including one from a Department of Medicine faculty member {L('UCSF_FRESNO','UCSF Fresno')}. "
     "Check each shortlisted program's page for similar rules.",
     "observership_start-120d", 0, "UCSF_FRESNO"),
    ("Screen paid brokers for red flags",
     f"Brokers such as AMOpportunities hide prices until you create an account {L('AMO','AMO')}, while AMA guidance says observerships are not intended to be organized for profit {L('AMA_GUIDE','AMA')}. "
     f"“Hands-on” work offered to a graduate on a B visa conflicts with the student-only clerkship rule {L('TEMPLE','Temple ISSS')}. "
     "Other red flags: guaranteed letters, unnamed preceptors, payment into personal accounts, no written refund policy.",
     "start+14d", 0, "AMA_GUIDE"),
    ("Treat virtual or telehealth rotations as filler",
     f"Virtual rotations are sold by brokers {L('AMO_VIRTUAL','AMO')}, but no program-director survey data shows they are valued. "
     "Do not let them replace in-person observership time.",
     "start+14d", 0, "AMA_OBS"),
    ("Do 4–8 weeks of observership in May–August",
     f"Plan blocks so letters can be uploaded by September; Missouri and SUNY Upstate do not start blocks in July {L('MU_NEURO','Missouri')} {L('UPSTATE','Upstate')}. "
     "Present cases verbally where allowed, read on your patients, and ask for mid-rotation feedback.",
     "observership_start", None, "AMA_OBS"),
    ("Ask for a strong, specific letter near the end of each block",
     f"Specialty-specific letters were used by 86% of programs for interview selection in 2026 {L('NRMP_PD26','NRMP 2026')}. "
     f"Give the attending your CV and highlights; letters are uploaded by the writer through ERAS, not by you {L('AAMC_ERAS_TL','AAMC ERAS')}. "
     "Indian faculty letters can fill remaining slots.",
     "observership_start+21d", 0, "AAMC_ERAS_TL"),
    ("If offered a US research post, check the visa first",
     f"J-1 research-scholar status is a different category from the ECFMG clinical J-1 {L('MAYO_J1','Mayo Clinic')}. "
     "It may carry 212(e) and a 24-month bar on repeat research-scholar status (inference, verify with an immigration attorney before accepting).",
     "start+30d", None, "MAYO_J1"),
])

TASKS["specialty_choice"] = ("spec", [
    ("Know your baseline odds",
     f"In 2026, 11,944 non-US IMGs applied and 56.4% matched to PGY-1 {L('NRMP_2026_DATA','NRMP 2026')}; those needing visa sponsorship matched at 54.4%, a five-year low {L('NRMP_2026_PR','NRMP')}. "
     f"Non-US IMG applicants rose from 7,864 in 2022 {L('NRMP_2026_DATA','NRMP 2026')}. "
     "US programs do not use your MBBS percentage or NEET PG rank.",
     "start", 0, "NRMP_2026_PR"),
    ("Make categorical Internal Medicine your primary specialty",
     f"Non-US IMGs took 3,448 categorical IM PGY-1 positions in 2026, 30.8% of the total {L('NRMP_2026_DATA','NRMP 2026')}. "
     f"Matched non-US IMGs in IM averaged Step 2 CK 248 vs 242 for unmatched and ranked 6.1 vs 2.5 contiguous programs (2024) {L('NRMP_CO24','NRMP 2024')}.",
     "step2ck_date+28d", 0, "NRMP_CO24"),
    ("Add Family Medicine as a parallel safety",
     f"FM gave non-US IMGs 962 positions (17.5%) in 2026 {L('NRMP_2026_DATA','NRMP 2026')}; matched vs unmatched Step 2 CK was 231 vs 227 in 2024 {L('NRMP_CO24','NRMP 2024')}. "
     f"Many community FM programs list no visa sponsorship, so check each one {L('RMAI_CA','ResidencyMatch.ai')}.",
     "step2ck_date+28d", 0, "NRMP_2026_DATA"),
    ("Add Peds, Psychiatry, Neurology or Pathology only with real interest",
     f"2026 non-US IMG shares: Pediatrics 21.9%, Psychiatry 8.8%, Neurology 17.4%, Pathology 26.7% {L('NRMP_2026_DATA','NRMP 2026')}. "
     f"Pathology programs most often report “often” interviewing non-US IMGs (at least 71%) {L('NRMP_PD26','NRMP 2026 PD Survey')}. "
     "Each needs specialty-specific letters, so add one only if an observership shows genuine interest.",
     "observership_start+28d", 0, "NRMP_2026_DATA"),
    ("Avoid low-IMG fields this cycle",
     f"Non-US IMGs filled only 4.4% of Anesthesiology, 7.1% of categorical Surgery, 2.7% of OB-GYN, 2.9% of EM and 2.0% of PM&R PGY-1 positions in 2026 {L('NRMP_2026_DATA','NRMP 2026')}. "
     f"Only 26.6% of non-US IMGs preferring General Surgery matched to it in 2024 {L('NRMP_CO24','NRMP 2024')}.",
     "step2ck_date+28d", 0, "NRMP_2026_DATA"),
    ("Check year-of-graduation filters",
     f"“Graduated within 5 years” is a common filter, for example at Highland IM {L('FREIDA_HIGHLAND','FREIDA')} and UCSF-Fresno IM {L('UCSF_FRESNO','UCSF Fresno')}. "
     "If you graduated 4–5 years before the Match, slipping a cycle may put you outside these programs.",
     "start", 0, "FREIDA_HIGHLAND"),
    ("Set your specialty mix by Step 2 CK score band",
     f"Naadi estimates (judgment built from NRMP data {L('NRMP_CO24','NRMP 2024')}, not NRMP figures): about 250 gives roughly 65–80% IM match chance; about 240 gives 45–60%, or 60–70% with FM in parallel; below 235, apply FM-heavy. "
     "Observership-only exposure or a graduation year 4–5 years back lowers each band by about 10–15 points.",
     "step2ck_date+28d", 0, "NRMP_STEP2_BASE"),
    ("Use NRMP's interactive Step 2 CK report",
     f"NRMP's Step 2 CK baseline report lets you filter by non-US IMG and specialty to see matched and unmatched counts by score {L('NRMP_STEP2_BASE','NRMP')}. "
     f"The 2026 non-US IMG Charting Outcomes report (Jul 2026) is now annual {L('NRMP_CO26','NRMP')}.",
     "step2ck_date+28d", 0, "NRMP_STEP2_BASE"),
    ("Decide your fallback trigger for a later Match",
     "Switch to the next cycle if Step 1 is not safely passed by about end of March of the application year, Step 2 CK cannot be sat by about July in your target range, or no B-1 or observership is possible before August. "
     f"The cost is one more year since graduation against “within 5 years” filters {L('UCSF_FRESNO','UCSF Fresno')}. Do documented clinical work in India to avoid an unexplained gap.",
     "date:2027-03-31", 0, "UCSF_FRESNO"),
])

TASKS["eras_application"] = ("eras", [
    ("Note when MyERAS opens",
     f"The 2027 ERAS season opened 4 Jun 2026; 2028 dates are not yet published, so early June 2027 is inferred {L('AAMC_ERAS_TL','AAMC')}. "
     "Create your MyERAS account and start the application in the first week.",
     "eras_open", 0, "AAMC_ERAS_TL"),
    ("Buy your ERAS token from ECFMG",
     f"The ERAS token is listed at $165 {L('IMGPREP','IMGPrep')}; some guides say $185 (verify). "
     f"ECFMG released 2027 tokens on 24 Jun 2026 {L('LECTURIO_ERAS','Lecturio, secondary')}.",
     "eras_open+21d", 165, "IMGPREP"),
    ("Request USMLE transcripts through FSMB",
     f"USMLE transcripts for ERAS are reported to come via FSMB from June 2026 {L('LECTURIO_ERAS','Lecturio, secondary')}. "
     "Request them as soon as MyERAS is open.",
     "eras_open+21d", None, "LECTURIO_ERAS"),
    ("Build a 100–150 IM and ~30 FM program list",
     f"Verify each program's J-1 policy and graduation-year rule in FREIDA, e.g. Highland IM is J-1 only with a 5-year limit {L('FREIDA_HIGHLAND','FREIDA')}. "
     "Naadi analysis: most IMG interviews come from IMG-heavy states such as NY, NJ, MI, OH, PA, IL and TX.",
     "eras_submit-60d", 0, "FREIDA_HIGHLAND"),
    ("Budget ERAS application fees",
     f"ERAS charges $11 per program for the first 30 and $30 each from the 31st, resetting per specialty {L('LECTURIO_ERAS','Lecturio')}. "
     "100 IM programs = $2,430; 150 = $3,930; add about 30 FM programs at $330.",
     "eras_submit-30d", 2760, "LECTURIO_ERAS"),
    ("Plan your program signals",
     f"2027 signals: IM 3 gold + 12 silver; FM 5; Pediatrics 5; Psychiatry 10; Neurology 8; Pathology 5 {L('AAMC_SIGNAL','AAMC')}. "
     f"Some programs ask for signals instead of letters of interest, e.g. Kern Medical IM {L('KERN','Kern')}. "
     "Spend gold signals on realistic IMG-friendly programs, not UCSF or Stanford.",
     "eras_submit-21d", 0, "AAMC_SIGNAL"),
    ("Write the personal statement; buy at most one review",
     "Keep paid help to one personal-statement review and 2–3 mock interviews with a written scope and refund policy. "
     f"Match A Resident has operated since 2004 and been BBB-accredited since 2011 but has a no-refund-after-access policy {L('BBB_MAR','BBB')} and mixed reviews {L('SDN_MAR','SDN')}.",
     "eras_submit-45d", None, "BBB_MAR"),
    ("Confirm all letters are uploaded",
     f"Specialty-specific letters were used by 86% of programs for interview selection in 2026 {L('NRMP_PD26','NRMP 2026')}. "
     "Check MyERAS that each US letter writer has uploaded before you submit.",
     "eras_submit-7d", 0, "AAMC_ERAS_TL"),
    ("Submit on day one",
     f"In the 2027 cycle applicants could submit from 2 Sep 2026 and programs began reviewing on 23 Sep {L('AAMC_ERAS_TL','AAMC')}; 2028 dates (about 1–2 Sep 2027) are inferred. "
     "IMG applications sent after review opens lose ground.",
     "eras_submit", None, "AAMC_ERAS_TL"),
    ("Register for the NRMP Main Match",
     f"For the 2027 Match, registration opened 15 Sep 2026 and the standard deadline is 29 Jan 2027, with a $50 late fee after {L('NRMP_CAL','NRMP')}. "
     "The base registration fee was not captured in the sources (verify on NRMP).",
     "eras_submit+14d", None, "NRMP_CAL"),
])

TASKS["interviews_ranking"] = ("intv", [
    ("Prepare for an October–January interview season",
     f"Interviews typically run October to January {L('LECTURIO_ERAS','Lecturio')}; most IM programs have remained largely virtual (verify). "
     "Set up a quiet room, reliable internet and a neutral background before invitations arrive.",
     "eras_submit+21d", 0, "LECTURIO_ERAS"),
    ("Do 2–3 mock interviews",
     f"Program directors rate interpersonal skills (89%, mean 4.8/5) and interview interactions (87%, 4.8) as top ranking factors {L('NRMP_PD24','NRMP 2024 PD Survey')}. "
     "Practise explaining your observership cases and why you want this specialty.",
     "eras_submit+30d", None, "NRMP_PD24"),
    ("Track invitations and aim for six or more ranks",
     f"Matched non-US IMGs in IM ranked a mean of 6.1 contiguous programs vs 2.5 for unmatched (2024) {L('NRMP_CO24','NRMP 2024')}. "
     "Log every invitation in Naadi so you can see early whether you need to widen your list.",
     "eras_submit+100d", 0, "NRMP_CO24"),
    ("Ask each program which visas it sponsors",
     f"Programs differ: Highland IM sponsors J-1 only {L('FREIDA_HIGHLAND_BEN','FREIDA')}, while Kaiser NorCal Oakland IM sponsors J-1 and H-1B {L('FREIDA_KAISER','FREIDA')}. "
     "Confirm in writing before ranking.",
     "rol_deadline-30d", 0, "FREIDA_HIGHLAND_BEN"),
    ("Make sure both Step passes are in by the ROL deadline",
     f"IMGs must pass Step 1 and Step 2 CK by the NRMP Rank Order List certification deadline {L('PATHWAYS27','ECFMG')}. "
     f"You also need an accepted, unexpired Pathway at that deadline {L('PW_EXP','ECFMG')}.",
     "rol_deadline-30d", 0, "PATHWAYS27"),
    ("Rank every program you would attend",
     f"Matched IMGs ranked about 2.5 times as many programs as unmatched IMGs {L('NRMP_CO24','NRMP 2024')}. "
     "Rank in true preference order, and leave off only programs you would not attend.",
     "rol_deadline-7d", 0, "NRMP_CAL"),
    ("Certify your Rank Order List",
     f"For the 2027 Match, ranking opens 1 Feb 2027 and the deadline is 3 Mar 2027 at 9 PM EST {L('NRMP_CAL','NRMP')}; the 2028 deadline (about early March) is inferred. "
     "Certify a few days early.",
     "rol_deadline-3d", 0, "NRMP_CAL"),
])

TASKS["match_day"] = ("match", [
    ("Check your Match status on Monday",
     f"In the 2027 Match, Match status is released 15 Mar 2027 {L('NRMP_CAL','NRMP')}. "
     "This tells you whether you matched, not where.",
     "match_day-4d", 0, "NRMP_CAL"),
    ("If unmatched, enter SOAP",
     f"SOAP runs 15–18 Mar in the 2027 Match {L('NRMP_CAL','NRMP')}. "
     "Have your ERAS documents ready and filter offers for J-1 sponsorship before accepting.",
     "match_day-4d", None, "NRMP_CAL"),
    ("Match Day",
     f"Match Day is 19 Mar 2027 for the 2027 Match {L('NRMP_CAL','NRMP')}; 2028's (likely Friday 17 Mar) is inferred. "
     f"In 2026, 42.3% of non-US IMGs matched to their first-choice program {L('NRMP_2026_DATA','NRMP 2026')}.",
     "match_day", 0, "NRMP_CAL"),
    ("Sign the contract or offer letter",
     f"Your contract or offer letter is part of the financial evidence for the J-1 interview {L('EVSP_PRE','Intealth')}. "
     "Sign and return it promptly and keep a PDF in your document vault.",
     "match_day+7d", 0, "EVSP_PRE"),
    ("Ask the program to start your Intealth J-1 application",
     f"The program's Training Program Liaison (TPL) initiates the Intealth J-1 application and receives the DS-2019 with you in MyIntealth {L('EVSP_PRE','Intealth')}. "
     "Tell the coordinator early if your visa interview may fall after mid-June.",
     "match_day+3d", 0, "EVSP_PRE"),
    ("Ask GME which state training license you need",
     f"Rules differ: ACGME residents in New York need no license {L('NYSED','NYSED')}, Texas requires a Physician-in-Training permit {L('TMB','TMB')}, Illinois a temporary license that takes 6–8 weeks {L('RUSH','Rush GME')}, and California a PTL within 180 days of starting {L('MBC','MBC')}.",
     "match_day+7d", 0, "MBC"),
    ("If you did not match, plan the next cycle now",
     f"Many IMG-friendly programs filter at “graduated within 5 years” {L('FREIDA_HIGHLAND','FREIDA')}. "
     "Use the year for a higher-yield profile: more US exposure, Step 3 if eligible, and documented clinical work in India.",
     "match_day+14d", None, "FREIDA_HIGHLAND"),
])
