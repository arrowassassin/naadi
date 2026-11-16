from urls import L

# Task tuple: (title, detail_md, due_rule, cost_usd or None, official_url key)
TASKS = {}

TASKS["eligibility"] = ("elig", [
    ("Verify your school's WDOMS Sponsor Note",
     f"ECFMG requires your school to be listed in the World Directory of Medical Schools with an ECFMG Sponsor Note that includes your graduation year; with no Sponsor Note you are not eligible {L('ECFMG_REQ','ECFMG')}. "
     f"Search your college on WDOMS and compare the exact institution name and graduation year on your diploma (college vs parent-university names can differ) {L('SPONSOR_NOTES','WDOMS')}. "
     "School IDs and year ranges quoted by coaching sites or AI roadmaps have not been independently verified, so check them yourself (verify).",
     "start", 0, "SPONSOR_NOTES"),
    ("Confirm basic ECFMG eligibility",
     f"You need credit for at least 4 academic years and must hold your final medical diploma {L('ECFMG_REQ','ECFMG')}. "
     f"USMLE Step 1 and Step 2 CK also require a school listed in WDOMS as meeting ECFMG eligibility requirements {L('USMLE_ELIG','USMLE')}.",
     "start", 0, "ECFMG_REQ"),
    ("Check passport validity",
     f"Your J-1 interview will require a passport valid at least 6 months beyond the DS-2019 end date {L('EVSP_PRE','Intealth')}. "
     "If yours would expire within 6 months of your expected training end date, renew it now, before the passport number is entered into MyIntealth, FSMB, OET and visa records.",
     "start", None, "EVSP_PRE"),
    ("Open a MyIntealth account",
     f"The MyIntealth account costs $110, which includes a $50 NotaryCam notarization {L('IMGPREP','IMGPrep')}. "
     f"Your OET booking must carry your MyIntealth ID so results reach ECFMG {L('FAQ27','2027 FAQ')}. "
     "Expect lead time for the identity and notarization steps (timing inferred, verify).",
     "start", 110, "ECFMG_IB"),
    ("Submit the Application for ECFMG Certification",
     f"The application fee is $580 (about ₹51,000) {L('IMGPREP','IMGPrep')}. "
     f"To apply for a 2027 Pathway by 31 Jan 2027, this application must already be “Accepted” or “Pending Credential Verification” in MyIntealth {L('PATHWAYS27','ECFMG')}. "
     "Submit it in your first week so the Pathway window stays open.",
     "start+7d", 580, "ECFMG_REQ"),
    ("Upload diploma and transcript for verification",
     f"Diploma and transcript verification cost $110 each {L('IMGPREP','IMGPrep')}. "
     f"All credentials must be uploaded via MyIntealth; copies sent by mail, courier or email are not accepted {L('ECFMG_IB','ECFMG')}. "
     f"ECFMG verifies the diploma directly with your school and requests the final transcript from it {L('ECFMG_REQ','ECFMG')}.",
     "start+7d", 220, "ECFMG_IB"),
    ("Alert your college registrar to the verification request",
     f"ECFMG performs primary-source verification with the school itself {L('ECFMG_REQ','ECFMG')}. "
     "No sourced data exists on how quickly Indian universities answer these requests (verify), so tell the registrar or dean's office to expect it and follow up weekly until it is returned.",
     "start+10d", 0, "ECFMG_REQ"),
    ("Register for USMLE through FSMB, not ECFMG",
     f"USMLE services moved from ECFMG to FSMB in January 2026; MyIntealth still handles credentials {L('ECFMG_NEWS_PW','ECFMG News')}. "
     "Older guides telling you to apply for Steps inside ECFMG's system are out of date.",
     "start+7d", 0, "ECFMG_NEWS_PW"),
    ("Check whether you qualify for Pathway 1",
     f"Pathway 1 needs a license or registration to practise without supervision that was valid on or after 1 Jan 2022; it need not be current, but supervised, training or restricted licenses do not count {L('PW1','ECFMG Pathway 1')}. "
     "ECFMG has not stated whether Indian NMC or State Medical Council permanent registration counts, so email ECFMG to confirm before paying (verify).",
     "start+7d", 0, "PW1"),
    ("Check whether you qualify for Pathway 3",
     f"Pathway 3 requires graduation on or after 1 Jan 2024 from a school accredited by a WFME-recognized agency, and a school official must attest to your clinical skills {L('PW35','ECFMG Pathways 3–5')}. "
     f"Check that your college appears on ECFMG's “Medical Schools Eligible for 2027 Pathways” list {L('PW35','ECFMG')}.",
     "start+7d", 0, "PW35"),
    ("Request licensing documents from the issuing authority",
     f"For Pathways, licensing documents must come directly from the issuing authority by email or post/courier {L('FAQ_OLD','ECFMG FAQ')}. "
     "Some guides say good-standing documents must be issued within 90 days; that wording is unverified (verify with ECFMG).",
     "start+14d", None, "FAQ_OLD"),
    ("Note the 7-year exam window and attempt limits",
     f"All exams for ECFMG certification must be passed within seven years of the first exam passed, and ECFMG sends no reminders {L('ECFMG_TIMELIMIT','ECFMG')}. "
     f"You may take a Step at most 3 times in 12 months; 4 failed attempts at any Step make you ineligible for USMLE {L('USMLE_ELIG','USMLE')}.",
     "start", 0, "ECFMG_TIMELIMIT"),
])

TASKS["step1"] = ("s1", [
    ("Apply for Step 1 in the FSMB portal",
     f"The 2026 fee for testing in India is $695 plus a $210 international surcharge, about ₹80,500 {L('IMGPREP','IMGPrep')}. "
     "Whether 18% GST applies, and the exact eligibility-period length, are unverified (verify on FSMB). "
     "Pick an eligibility period that covers your target month plus a few weeks of slack.",
     "start+7d", 905, "USMLE_ELIG"),
    ("Book your Prometric seat as soon as the permit arrives",
     f"Indian Prometric USMLE centres are listed in Ahmedabad, Prayagraj, Bengaluru, Chennai, Hyderabad, Kolkata, Mumbai and Gurugram {L('CAREERS360','Careers360')}. "
     f"Application approval does not reserve a seat and preferred centres may be full, so search several cities {L('CAREERS360','Careers360')}.",
     "step1_date-60d", 0, "CAREERS360"),
    ("Buy UWorld Step 1",
     f"UWorld Step 1 costs $459 for 90 days with one self-assessment, or $519 for 180 days with two (about ₹41–46k) {L('UWORLD1','UWorld')}. "
     "Choose the 180-day plan if your exam date might slip.",
     "step1_date-60d", 459, "UWORLD1"),
    ("Take a baseline NBME CBSSA",
     f"NBME sells CBSSA forms for Step 1 through MyNBME {L('NBME_SA','NBME')}; a Sept 2025 article put them at $60 each {L('ELITE','Elite Medical Prep')}. "
     "Take one early to see where your NEET PG knowledge stands against USMLE-style questions.",
     "step1_date-58d", 60, "NBME_SA"),
    ("Run UWorld timed and random every day",
     f"The plan is about 80–120 UWorld questions a day, timed and random by week 2, with careful review {L('UWORLD1','UWorld')}. "
     f"Use free Mehlman HY PDFs {L('MEHLMAN','TestPrepPal')} and Divine Intervention podcasts {L('DIVINE','Divine')} only for weak areas.",
     "step1_date-56d", 0, "UWORLD1"),
    ("Schedule NBME forms every 1–2 weeks",
     f"Informal, student-reported data describe a CBSSA of about 62–68 as the “low passing range” and about 70 as a safer bar {L('ELITE','Elite Medical Prep')}. "
     "Sit only after two fresh forms land there. Plan 3–4 forms in total.",
     "step1_date-42d", 180, "NBME_SA"),
    ("Make the go / no-go decision",
     f"A failed attempt is permanent on your record, and many programs screen for first-attempt passes; a passed Step cannot be retaken {L('USMLE_ELIG','USMLE')}. "
     "If your last two forms are below range, move the exam by up to 4 weeks rather than risk a fail.",
     "step1_date-14d", 0, "USMLE_ELIG"),
    ("Take the Free 120 3–7 days before the exam",
     f"USMLE publishes free sample questions for each Step {L('USMLE_PREP','USMLE')}. "
     "Whether the “Free 120” name and format changed under the 2026 software is unverified (verify).",
     "step1_date-5d", 0, "USMLE_PREP"),
    ("Know the test-day format",
     f"From 14 May 2026 Step 1 is reported as 14 blocks of 30 minutes, 20 questions each, 280 items in an 8-hour day {L('LECTURIO_2026','Lecturio')} (secondary; verify on usmle.org). "
     "Practise full-length days at this block length.",
     "step1_date-21d", 0, "USMLE_PREP"),
    ("Avoid pirated material and exam recalls",
     f"Using exam “recalls” risks breaking USMLE rules, and NBME has publicly disclosed cheating cases {L('MDEDGE','MDedge')}. "
     "Pirated First Aid or UWorld PDFs circulating on Telegram also infringe copyright.",
     "start", 0, "USMLE_ELIG"),
    ("Vet any coaching programme before paying",
     f"No coaching provider publishes an independently verified match rate; one common “top centres in India” ranking is published by the company it ranks first {L('MENDEL','Mendel')}. "
     f"Treat “guaranteed results” {L('GATEIIT','example')} and success rates without a denominator {L('ACADEMICALLY','example')} as red flags. "
     f"If scores stall, hourly tutoring is an option; Mehlman packages range $185–$5,160 {L('MEHLMAN','TestPrepPal')}.",
     "start+3d", None, "USMLE_ELIG"),
    ("Record your result and update the plan",
     f"The 2025 first-time Step 1 pass rate was 75% for IMGs vs 93% for US MD students {L('USMLE_PERF','USMLE')}. "
     "Score reporting typically takes about 3–4 weeks (unverified). A pass unlocks most observership applications.",
     "step1_date+28d", 0, "USMLE_SCORES"),
])

TASKS["oet_pathway"] = ("oet", [
    ("Book OET Medicine (paper) for 19 Dec 2026",
     f"The last December 2026 paper sitting for India is reported as 19 Dec, with bookings closing 25 Nov and results by about 5 Jan 2027 {L('TALKDRILL','TalkDrill, secondary')}. "
     f"The fee is AUD 587 (about ₹34–39k, ~$380), including a AUD 7 booking fee {L('LEAP_OET','LeapScholar')}. "
     f"ECFMG recommends testing on or before the last scheduled December 2026 date for the 2027 Pathways {L('FAQ27','2027 FAQ')}.",
     "date:2026-11-25", 380, "FAQ27"),
    ("Enter your MyIntealth ID (not your USMLE ID) on the OET booking",
     f"The 2027 FAQ says you must provide your MyIntealth ID to OET so results go to ECFMG, and warns against entering the wrong ID {L('FAQ27','2027 FAQ')}. "
     "Create the MyIntealth account before booking.",
     "date:2026-11-25", 0, "FAQ27"),
    ("Know the score rule: 350/350/350/300 in one sitting",
     f"ECFMG requires 350 in Listening, Reading and Speaking and 300 in Writing, all from one administration {L('FAQ_OLD','ECFMG FAQ')}. "
     f"Every applicant must take OET regardless of native language, so an English-medium MBBS gives no waiver {L('FAQ_OLD','ECFMG FAQ')}. "
     f"A sub-test-only retake (AUD 315/415/515 for 1/2/3 sub-tests {L('LEAP_OET','LeapScholar')}) probably does not satisfy the one-sitting rule (verify).",
     "oet_date-30d", 0, "ECFMG_CS"),
    ("Test at a venue, not OET@Home",
     f"OET@Home is accepted only for applicants with no physical test venue in their country {L('FAQ27','2027 FAQ')}. "
     f"OET has 19 test venues in India {L('OET_VENUES','OET')}, so Indian applicants must test in person.",
     "date:2026-11-25", 0, "FAQ27"),
    ("Fallback: computer-based December slot",
     f"Computer-at-venue OET runs in Monday–Saturday blocks, bookings close 7 days before, and results can arrive in about 48 hours {L('TALKDRILL','TalkDrill, secondary')}. "
     "Which Indian venues offer computer-based OET in December 2026 is unconfirmed (verify).",
     "date:2026-12-10", 380, "TALKDRILL"),
    ("Fit OET practice into your Step 1 plan",
     "Add about 45 minutes a day of OET practice in weeks 2–5 of the Step 1 sprint and keep the day before and the day of OET light. "
     f"Rescheduling costs AUD 120 if more than 7 days ahead, AUD 200 within 7 days {L('LEAP_OET','LeapScholar')}.",
     "oet_date-28d", 0, "LEAP_OET"),
    ("Submit the 2027 Pathway application by 31 Jan 2027 (ET)",
     f"The application must be submitted no later than 31 Jan 2027, Eastern Time {L('PATHWAYS27','ECFMG')}. "
     f"The 2027 fee is reported as $945, non-refundable (up from $925) {L('MATCHGUY','The Match Guy, secondary')}. "
     f"No Step 1 or Step 2 CK pass is needed to apply {L('FAQ27','2027 FAQ')}.",
     "date:2027-01-31", 945, "PATHWAYS27"),
    ("Confirm ECFMG has received your OET scores",
     f"ECFMG must receive scores directly from OET by 31 Jan 2027; later results may not be processed in time {L('PATHWAYS27','ECFMG')}. "
     f"OET results typically arrive about 10 business days after the test {L('FAQ_OLD','ECFMG FAQ')}.",
     "date:2027-01-20", 0, "PATHWAYS27"),
    ("Pathway 3 only: get the school attestation in",
     f"A school official must attest to your clinical skills; electronic attestations are due 15 Feb 2027 and paper forms by 31 Jan 2027 {L('PW35','ECFMG')}. "
     f"Schools submit through ECFMG's Clinical Skills Evaluation and Attestation Portal {L('ECFMG_NEWS_5551','ECFMG News')}.",
     "date:2027-02-15", 0, "PW35"),
    ("Pathway 6 fallback: arrange six in-person Mini-CEX encounters",
     f"Pathway 6 is for applicants who do not meet Pathways 1–5 and needs six real, in-person encounters, each evaluated by a licensed physician; telemedicine and standardized patients are not accepted {L('FAQ27','2027 FAQ')}. "
     f"Mini-CEX forms are reported due 15 Feb 2027 {L('MATCHGUY','The Match Guy, secondary')}, and evaluations from a 2026 application cannot be reused {L('FAQ27','2027 FAQ')}.",
     "date:2027-02-15", None, "PATHWAYS27"),
    ("Email ECFMG to confirm your Pathway's expiry",
     f"A secondary source reports 2027 Pathways expire 31 Dec 2029 {L('MATCHGUY','The Match Guy')}; ECFMG's official table stops at the 2026 season, which expires 31 Dec 2028 {L('PW_EXP','ECFMG')}. "
     f"You must hold an accepted, unexpired Pathway at the Rank Order List deadline {L('PW_EXP','ECFMG')}. "
     "Ask ECFMG in writing for the exact expiry and whether a 2027 Pathway stays valid if you skip the 2027 Match (verify).",
     "date:2027-02-28", 0, "PW_EXP"),
    ("Watch for a 2028 Pathways announcement",
     f"ECFMG publishes Pathways one year at a time and tells future applicants to “monitor the ECFMG website” {L('FAQ27','2027 FAQ')} {L('ECFMG_NEWS_PW','ECFMG News')}. "
     "If you missed the 2027 window, a 2028 Pathway (not yet announced) is the fallback; the 2027 window accepted OET taken on or after 1 Jan 2025, so a Dec 2026 OET would probably still count (inferred, verify).",
     "date:2027-06-01", 0, "ECFMG_NEWS_PW"),
])

TASKS["step2ck"] = ("s2ck", [
    ("Apply for Step 2 CK in the FSMB portal",
     f"The 2026 fee in India is $695 plus a $235 international surcharge, about ₹82,800 before any GST {L('IMGPREP','IMGPrep')}. "
     "Apply as soon as Step 1 is done so the permit is ready for your 12-week block.",
     "step1_date+7d", 930, "USMLE_ELIG"),
    ("Book the Prometric seat",
     f"Step 2 CK uses the same Indian Prometric centres as Step 1 {L('CAREERS360','Careers360')}. "
     "Book early in the eligibility period and keep a fallback city.",
     "step2ck_date-84d", 0, "CAREERS360"),
    ("Buy UWorld Step 2 CK",
     f"UWorld CK costs $459 for 90 days or $519 for 180 days (about ₹41–46k); 180-day plans add a one-time reset {L('UWORLD2','UWorld')}. "
     "This is the core question bank for CK.",
     "step2ck_date-84d", 459, "UWORLD2"),
    ("Weeks 1–2: baseline CCSSA and US conventions",
     f"Take a baseline NBME CCSSA (sold via MyNBME) {L('NBME_SA','NBME')}. "
     "Spend these weeks learning US management conventions: screening, consent, ethics and patient safety, which differ from Indian practice.",
     "step2ck_date-84d", 60, "NBME_SA"),
    ("Weeks 3–8: 80–120 timed random questions a day",
     f"Work UWorld CK timed and random {L('UWORLD2','UWorld')}, use Divine Intervention {L('DIVINE','Divine')} and Mehlman CK PDFs {L('MEHLMAN','TestPrepPal')} for weak areas, and Anki for misses. "
     "Take a CCSSA about every 2 weeks.",
     "step2ck_date-70d", 120, "UWORLD2"),
    ("Weeks 9–11: finish UWorld and take UWSA 1 and 2",
     f"UWorld self-assessments cost $50 each or $120 for the bundle {L('UWORLD2','UWorld')}. "
     f"Aim for 245–250+ on predictors: matched non-US IMGs in IM averaged 248 vs 242 for unmatched in 2024 {L('NRMP_CO24','NRMP')}.",
     "step2ck_date-21d", 120, "UWORLD2"),
    ("Know the format and pass mark",
     f"The pass mark is 218 for tests on or after 1 Jul 2025, and the mean is about 250 (SD 15) {L('IATROX_CK','iatroX, secondary')}. "
     f"From 7 May 2026 the exam is reported as 16 × 30-minute blocks, 316 items, in a 9-hour day {L('LECTURIO_2026','Lecturio, secondary')}.",
     "step2ck_date-30d", 0, "USMLE_SCORES"),
    ("Week 12: Free 120, a full-length day, weak-topic repair",
     f"Use USMLE's free sample questions {L('USMLE_PREP','USMLE')} and do one full-length stamina day at the 9-hour length. "
     "Sit only if predictors are in range; otherwise push 2–4 weeks.",
     "step2ck_date-7d", 0, "USMLE_PREP"),
    ("Go / no-go: do not rush the score that matters",
     f"A passed Step cannot be retaken for a higher score {L('USMLE_ELIG','USMLE')}, and 89% of programs use the CK score for interview decisions {L('NRMP_PD26','NRMP 2026')}. "
     f"Of programs that require Step 2 CK, 32% set a target score for IMGs {L('NRMP_PD26','NRMP 2026')}.",
     "step2ck_date-14d", 0, "USMLE_ELIG"),
    ("Track the result and ECFMG certification",
     "The result is expected about 3–4 weeks after the exam (unverified). "
     f"ECFMG issues the certificate about two weeks after all requirements are met {L('FAQ_OLD','ECFMG FAQ')}. "
     f"The IMG first-time Step 2 CK pass rate was 90% in 2024–25 {L('USMLE_PERF','USMLE')}.",
     "step2ck_date+28d", 0, "USMLE_SCORES"),
])
