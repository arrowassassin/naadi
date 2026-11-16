from urls import L

POLICY_WATCH = f"""| Watch item | Status as of 15 Nov 2026 | What to do | Sources |
|---|---|---|---|
| **H-1B $100,000 fee (Proclamation 10973)** | Applies to new petitions for physicians outside the US; not to J-1→H-1B change of status {L('FSMB_ATTY','FSMB/attorneys')}. Vacated 8 Jun 2026; First Circuit denied a stay 24 Jul {L('CLARKHILL','Clark Hill')}. Renewed 18 Sep 2026 to run to 21 Sep 2027 {L('KLASKO','Klasko')}. Renewal vacated and enjoined 30 Sep 2026 (N.D. Cal.) {L('FORBES','Forbes')}. **Not being collected, but litigation continues**; a proposed $103,265 fee rule (Aug 2026) is pending {L('FORBES','Forbes')}. A physician-exemption bill had no hearing as of May 2026 {L('AAPPR','AAPPR')} {L('CMA_BILL','CMA')}. | Treat H-1B-from-abroad as a bonus, J-1 as default. Re-check before ranking H-1B programs. | FSMB, Clark Hill, Klasko, Forbes |
| **J-1 fixed admission period (end of D/S)** | DHS final rule published 17 Jul 2026: fixed Admit Until Date (program end + 30 days, max 4 years per admission). Due 15 Sep 2026; **postponed, no new date** as of 23 Sep 2026 {L('UTHSCSA_DS','UTHSCSA OIS')} {L('KLASKO_DS','Klasko')}. | Check the I-94 at every entry; renew the passport early; extensions would need Form I-539 if the rule takes effect. | UTHSCSA OIS, Klasko |
| **MoHFW Statement of Need bond** | Each SoN needs a ₹10 lakh surety bond on ₹100 stamp paper with two sureties; return-to-India clause with ₹10 lakh + 12% interest demand (scan partly illegible, verify) {L('MOHFW_OM','MoHFW OM 2023')}. No NORI under any circumstance {L('CGISF','CGI SF')}. Processing time and fee: not published (verify). | Line up sureties before Match Day; apply the week after Match. | MoHFW, CGI SF |
| **Visa appointments and processing for Indian physicians** | 2026 reports of J-1 refusals, prolonged administrative processing and revoked visitor visas {L('SAH','South Asian Herald')}. Intealth briefed agencies and urged previously denied physicians to reapply in India (11 Jun 2026) {L('AAIM_INTEALTH','AAIM')}. 20+ societies reported missed 1 July starts {L('CANCERLETTER','The Cancer Letter')}. In-person, home-country interviews required since Sep 2025 {L('FAEGRE','Faegre Drinker')}. | Book B1/B2 now; J-1 steps the week after Match; consistent, truthful applications; public social media consistent with DS-160. | South Asian Herald, AAIM, Faegre |
| $250 Visa Integrity Fee | Legislated 2025; no official notice of collection at Indian posts as of 23 Oct 2026 (verify) {L('VISAVISA','visavisa.in')}. | Budget it as a possible extra. | visavisa.in |
| HHS J-1 waiver backlog | Months-long backlog 2025–26; no HHS timeline {L('KFF','KFF Health News')} {L('GILLIBRAND','Gillibrand letter')}. | Apply to several states' Conrad 30 programs. | KFF, Senate |
| 2028 ECFMG Pathways | Not announced {L('ECFMG_NEWS_PW','ECFMG News')}. | Lock a 2027 Pathway if eligible. | ECFMG |
| California AB 2386 | Died in Senate Appropriations 13 Aug 2026 {L('CALMATTERS','CalMatters')}. | No shortcut to a California license. | CalMatters |
"""

SCENARIOS = f"""| Scenario | Path | Bay Area practice |
|---|---|---|
| **Best** | Match 2028 on J-1; visa on time; 3-year IM residency to June 2031; California Conrad 30 slot at a Bay-adjacent shortage-area site or FQHC {L('HCAI','HCAI')}; fee-exempt J-1→H-1B change of status {L('FSMB_ATTY','FSMB/attorneys')}; early I-140 locks a priority date. | Bay Area-adjacent by 2031; free choice of job after about 2034 |
| **Typical** | J-1; waiver job in the Central Valley, a rural area or another state 2031–34; then H-1B. EB-2 India backlog {L('F1JOBS','F1Jobs')}. | Moves to the Bay Area after about 2034; green card in the late 2030s or later |
| **Worst** | Unmatched in 2028 and needs a second cycle; or J-1 interview delayed past 1 July {L('CANCERLETTER','The Cancer Letter')}; or no waiver job, forcing two years back in India {L('EVSP_GEN','ECFMG')}. | 2037 or later, or never |

*Scenarios are Naadi synthesis from the cited rules, not predictions.*
"""

BAY_PAY = f"""| Program | PGY-1 | PGY-2 | PGY-3 | Notes | Source |
|---|---|---|---|---|---|
| UCSF (2026–27, effective 1 Sep 2026) | $101,801 | $104,550 | $107,917 | Housing allowance folded into salary since 1 Jul 2024 | {L('UCSF_SAL','UCSF GME')} |
| Stanford Health Care (2026–27, pending new SHC-CIR contract; same as 2025–26) | $96,948.80 | $101,192 | $107,868.80 | 2025–26 base included $12k housing allowance; meal and phone allowances extra | {L('STANFORD_SAL','Stanford GME')} |
| Kaiser Permanente NorCal (Oakland) IM | $97,939 | $102,486 | $108,170 | Sponsors J-1 and H-1B | {L('FREIDA_KAISER','FREIDA')} |
| Alameda Health System–Highland IM | $83,905 | $89,975 | $93,767 | J-1 only, no H-1B | {L('FREIDA_HIGHLAND_BEN','FREIDA')} |
| *Benchmark:* AAMC national PGY-1 mean / Western mean | $68,166 / $77,649 | | | AAMC 2025 survey | {L('AMA_PAY','AMA')} |
"""

HUD = f"""| Area | 1BR Fair Market Rent, FY2027 (from 1 Oct 2026) |
|---|---|
| San Francisco | $3,003 |
| San Jose–Sunnyvale–Santa Clara | $2,778 |
| Oakland–Fremont | $2,290 |
| Stockton–Lodi | $1,628 |
| Fresno | $1,363 |
| Detroit (“Detroit–Warren–Livonia” per source; HUD's current area name may differ) | $1,167 |
| Cleveland, OH | $1,050 |

> **Verify before publishing:** these are secondary transcriptions from {L('STARTBIZ','StartBusinessByState')}, which cites HUD and the Federal Register of 1 Sep 2026. HUD's own query tool blocked automated fetching. Check each figure at huduser.gov. An FMR is roughly a 40th-percentile gross rent including utilities, not a typical listing price (standard HUD definition, not fetched).
"""

FIRST_YEAR = f"""| Item | Mid-cost city (e.g. Cleveland, Detroit) | Bay Area (Oakland, San Jose, SF) | Basis |
|---|---|---|---|
| PGY-1 gross salary | ~$68,166 (national mean); Central region $68,580 | $83,905 (Highland) to $101,801 (UCSF) | Sourced: {L('AMA_PAY','AMA')}, {L('FREIDA_HIGHLAND_BEN','FREIDA')}, {L('UCSF_SAL','UCSF')} |
| 1BR HUD FMR per month | $1,050 (Cleveland), $1,167 (Detroit) | $2,290 (Oakland), $2,778 (San Jose), $3,003 (SF) | Sourced, secondary, **verify**: {L('STARTBIZ','HUD via StartBusinessByState')} |
| 1BR HUD FMR per year | $12,600–14,004 | $27,480–36,036 | Arithmetic on the FMR row |
| Rent as % of gross | ~18% (Cleveland FMR on $68k) | ~27% (Oakland FMR on $97–102k) | Inference in source notes |
| Typical 1BR rent (pass-1 estimate) | $1,200–1,600 / month | $2,800–3,500 / month | **Unsourced planning estimate** |
| One-time setup total | ~$8k–15k | ~$10k–18k | **Unsourced planning estimate** (flight $700–1,200; deposit + first month 2–3× rent; furniture $1,500–3,000; car down payment $3,000–6,000 or $0 with SF transit; licensing $0–500, often reimbursed) |
| Net take-home per month | ~$4.5k–5.5k on PGY-1 averages with FICA exempt in early years | (not estimated) | **Unsourced estimate** |
| J-1 visa fee | $185 | $185 | {L('EVSP_PRE','Intealth')} |
| SEVIS I-901 | $220 | $220 | {L('ICE_I901','ICE')} |
| SSN | Free | Free | {L('SSA_PUB','SSA')} |
| J-2 spouse EAD (if needed) | $520 | $520 | {L('MIT_J2','MIT ISchO')} |
| MoHFW bond stamp paper | ₹100 (bond ₹10 lakh is contingent, not a cost) | same | {L('MOHFW_OM','MoHFW')} |
| Money from India | First ₹10 lakh/FY TCS-free; 20% TCS above for non-education purposes | same | {L('ICICI_LRS','ICICI')} |

Not sourced in any pass (do not invent): car insurance for drivers with no US history, phone plans, renter's insurance, food costs, gap-period health insurance cost, Central Valley program salaries.
"""

BUDGET_TO_MATCH = f"""Figures at about ₹89 per USD; excludes living costs in India.

| Item | USD | ≈ INR |
|---|---|---|
| MyIntealth $110 + ECFMG application $580 + verifications $220 {L('IMGPREP','IMGPrep')} | $910 | ₹0.81 L |
| 2027 Pathway {L('MATCHGUY','The Match Guy')} | $945 | ₹0.84 L |
| OET Medicine (AUD 587) {L('LEAP_OET','LeapScholar')} | ~$380 | ₹0.34–0.39 L |
| Step 1 + Step 2 CK (plus 18% GST if it applies, not verified) {L('IMGPREP','IMGPrep')} | $1,835–2,165 | ₹1.6–1.9 L |
| UWorld ×2, NBME forms, UWSAs {L('UWORLD1','UWorld')} | $1,500–1,800 | ₹1.3–1.6 L |
| B1/B2 visa fee {L('VISAVISA','visavisa.in')} | $185 | ₹0.16 L |
| Observerships, 8 weeks (UTHSCSA/Missouri up to UAB) {L('UAB','UAB')} {L('UTHSCSA','UTHSCSA')} | $4,350–8,850 | ₹3.9–7.9 L |
| Flights, housing, insurance during observerships | $3,000–7,000 | ₹2.7–6.2 L (**planning estimate**) |
| ERAS (100–150 IM + 30 FM) + token $165 (some sources say $185; verify) {L('LECTURIO_ERAS','Lecturio')} | $2,900–4,400 | ₹2.6–3.9 L |
| Pay-per-service ERAS help (optional) | $0–1,500 | ₹0–1.3 L |
| **Total** | **~$16,000–28,000** | **~₹14–25 lakh** |
| Optional Step 3 (~$955, unverified) + US stay; retake/contingency reserve | +$2,000–5,000 | +₹1.8–4.5 L |
"""

TIMELINE_2028 = f"""Target for a 2028 Match (dates marked *inferred* follow past cycles; ERAS 2028 is unpublished {L('AAMC_ERAS_TL','AAMC')}).

| When | Milestone |
|---|---|
| 16 Nov 2026 | Start Step 1 plan; open MyIntealth; book OET and the B1 interview |
| 19 Dec 2026 | OET Medicine (paper); results ~5 Jan 2027 {L('TALKDRILL','TalkDrill')} |
| Mid/late Jan 2027 | Step 1 (only if two fresh NBMEs ~65–70+) |
| By 31 Jan 2027 | 2027 Pathway application {L('PATHWAYS27','ECFMG')}; Pathway 3 electronic attestation by 15 Feb {L('PW35','ECFMG')} |
| Feb–May 2027 | Step 2 CK block (~12 weeks); apply to observerships |
| ~May 2027 | Step 2 CK |
| May–Aug 2027 | 4–8 weeks of observership; 2–3 US letters |
| ~Jun–Jul 2027 | ECFMG certificate once Step 1, Step 2 CK and Pathway are done |
| ~Early Jun 2027 (*inferred*) | MyERAS opens |
| ~1–2 Sep 2027 (*inferred*) | Submit ERAS on day one |
| Oct 2027–Jan 2028 | Interviews |
| ~Early Mar 2028 (*inferred*) | Rank Order List deadline (2027: 3 Mar) {L('NRMP_CAL','NRMP')} |
| ~17 Mar 2028 (*inferred*) | Match Day (2027: 19 Mar) |
| Mar–Jun 2028 | MoHFW SoN, Intealth J-1, J-1 interview in India |
| 1 Jul 2028 | Residency starts; California PTL due within 180 days {L('MBC','MBC')} |
"""

G = {}

G["eligibility"] = dict(
    mistakes=[
        f"Trusting a school ID or Sponsor Note year range from a coaching site instead of checking WDOMS yourself {L('SPONSOR_NOTES','WDOMS')}.",
        f"Leaving the ECFMG application until after Step 1 — a 2027 Pathway needs it “Accepted” or “Pending Credential Verification” by 31 Jan 2027 {L('PATHWAYS27','ECFMG')}.",
        f"Mailing or emailing documents — only MyIntealth uploads are accepted {L('ECFMG_IB','ECFMG')}.",
        f"Following pre-2026 guides that register USMLE through ECFMG; it is FSMB now {L('ECFMG_NEWS_PW','ECFMG News')}.",
        "Assuming Indian permanent registration automatically qualifies for Pathway 1 without asking ECFMG (verify).",
    ],
    sections=[("ECFMG certification: the four parts",
               f"1. Accepted application with diploma and transcript verified directly with your school via MyIntealth.\n2. Step 1 pass.\n3. Step 2 CK pass.\n4. An accepted, unexpired Pathway, which includes OET Medicine.\n\nSource: {L('ECFMG_REQ','ECFMG 2026 IB')}"),
              ("The four tracks that must converge", TIMELINE_2028)],
)

G["step1"] = dict(
    mistakes=[
        f"Sitting on a calendar date instead of readiness — a fail is permanent and a pass cannot be retaken {L('USMLE_ELIG','USMLE')}.",
        f"Assuming application approval reserves a Prometric seat; it does not {L('CAREERS360','Careers360')}.",
        f"Buying every lecture series; spend on questions and self-assessments {L('UWORLD1','UWorld')} {L('NBME_SA','NBME')}.",
        f"Using “recalls” or pirated PDFs {L('MDEDGE','MDedge')}.",
        f"Paying for coaching on the strength of self-published rankings {L('MENDEL','Mendel')}.",
    ],
    sections=[("Exams at a glance (2026)",
               f"""| Exam | Format / scoring | Where | Fee | Key rules |
|---|---|---|---|---|
| Step 1 | Pass/fail; 14 × 30-min blocks, 280 items {L('LECTURIO_2026','Lecturio')} | Prometric India: Kolkata, Mumbai, Chennai, Hyderabad, Bengaluru, Gurugram, Ahmedabad, Prayagraj {L('CAREERS360','Careers360')} | $695 + $210 ≈ ₹80,500 (+GST if it applies) {L('IMGPREP','IMGPrep')} | IMG first-time pass 75% (2025) {L('USMLE_PERF','USMLE')} |
| Step 2 CK | 3-digit; pass 218; mean ~250; 16 × 30-min blocks {L('IATROX_CK','iatroX')} | Same centres | $695 + $235 ≈ ₹82,800 | IMG first-time pass 90% |
| Step 3 | 2 days incl. CCS | US and territories only | ~$955 (unverified) | Needs ECFMG certificate {L('USMLE_ELIG','USMLE')} |
| OET Medicine | 350 L/R/S, 300 W, one sitting | 19 venues in India {L('OET_VENUES','OET')} | AUD 587 ≈ ₹34–39k {L('LEAP_OET','LeapScholar')} | Book with MyIntealth ID |

Attempt rules: max 3 attempts at a Step in 12 months; 4 failed attempts at any Step end eligibility; the 4th attempt must be ≥12 months after the 1st and ≥6 months after the most recent {L('USMLE_ELIG','USMLE')}."""),
              ("Lean resource stack",
               f"""| Item | Price | Use |
|---|---|---|
| UWorld Step 1, 90 days | $459 (1 UWSA); 180 days $519 (2 UWSAs) | Core Qbank {L('UWORLD1','UWorld')} |
| NBME CBSSA | ~$60 per form (Sep 2025 report) | 3–4 forms {L('NBME_SA','NBME')} {L('ELITE','Elite')} |
| UWSA bundle | $120 | Readiness {L('UWORLD1','UWorld')} |
| AMBOSS | $448/yr Qbank; ~$150/yr library | Optional second resource {L('AMBOSS','AMBOSS')} |
| Mehlman HY PDFs; Divine Intervention | Free | High-yield review {L('MEHLMAN','TestPrepPal')} {L('DIVINE','Divine')} |
| First Aid 2026; Boards & Beyond; Pathoma; Sketchy | Prices not verified | Gap-filling only |"""),
              ("Coaching red flags",
               f"“Guaranteed” results {L('GATEIIT','example')}; rankings written by the ranked company {L('MENDEL','example')}; success rates with no denominator or year {L('ACADEMICALLY','example')}; exam pass rates presented as match rates; letters promised before a rotation; “hands-on” work for a graduate on a B visa; payment into personal accounts; visa “guidance” from non-attorneys. No company can carry you “through the visa”: J-1 sponsorship comes from ECFMG/Intealth and H-1B from the hiring program.")],
)

G["oet_pathway"] = dict(
    mistakes=[
        f"Booking OET with your USMLE ID instead of your MyIntealth ID {L('FAQ27','2027 FAQ')}.",
        f"Planning on OET@Home — not accepted where a country has venues, and India has 19 {L('FAQ27','2027 FAQ')} {L('OET_VENUES','OET')}.",
        f"Thinking you need Step passes before applying for a Pathway — you don't {L('FAQ27','2027 FAQ')}.",
        f"Assuming a sub-test retake satisfies the one-sitting rule (probably not; verify) {L('FAQ_OLD','ECFMG FAQ')}.",
        f"Waiting for 2028 Pathways that have not been announced {L('ECFMG_NEWS_PW','ECFMG News')}.",
    ],
    sections=[("December 2026 OET on paper (India, secondary source)",
               f"""| Test date | Bookings close | Results by |
|---|---|---|
| 5 Dec 2026 | 11 Nov 2026 (closed) | 22 Dec 2026 |
| 19 Dec 2026 (Medicine) | 25 Nov 2026 | 5 Jan 2027 |

Computer and @Home bookings close 7 days before; results in as little as 48 hours (95% within 6 days). Source: {L('TALKDRILL','TalkDrill, verified against OET booking page per publisher')}. Verify on oet.com."""),
              ("Which Pathway?",
               f"""| Pathway | Fits if | Extra steps | Source |
|---|---|---|---|
| 1 | Unsupervised license/registration valid on/after 1 Jan 2022 | Ask ECFMG whether Indian permanent registration counts | {L('PW1','ECFMG')} |
| 3 | Graduated on/after 1 Jan 2024; school WFME-accredited and on the 2027 eligible list | School attestation: electronic by 15 Feb 2027 | {L('PW35','ECFMG')} |
| 6 | You fit none of 1–5 | Six real in-person Mini-CEX encounters by licensed physicians | {L('FAQ27','2027 FAQ')} |

Fees for OET + Pathway total about $1,325. Expiry: 2026 Pathways expire 31 Dec 2028 (official) {L('PW_EXP','ECFMG')}; 2027 Pathways reported to expire 31 Dec 2029 (secondary) {L('MATCHGUY','The Match Guy')}.""")],
)

G["step2ck"] = dict(
    mistakes=[
        f"Rushing CK the way Step 1 was rushed — the score is used by 89% of programs for interviews {L('NRMP_PD26','NRMP 2026')}.",
        "Studying Indian management protocols instead of US conventions (screening, consent, ethics, safety).",
        f"Sitting with predictors below target — a passed Step cannot be retaken {L('USMLE_ELIG','USMLE')}.",
        f"Forgetting the 9-hour, 16-block test day {L('LECTURIO_2026','Lecturio')}.",
    ],
    sections=[("12-week Step 2 CK plan",
               f"""| Weeks | Focus | Checkpoints |
|---|---|---|
| 1–2 | Baseline CCSSA; UWorld CK by system; US management conventions | Baseline score |
| 3–8 | 80–120 UWorld CK Qs/day, timed random; Divine + Mehlman CK for weak areas; Anki for misses | CCSSA every ~2 weeks |
| 9–11 | Finish UWorld; UWSA 1 and 2; remaining CCSSAs | Aim **245–250+** on predictors |
| 12 | Free 120, full-length day, weak-topic repair | Sit only if in range; else push 2–4 weeks |

Benchmarks: matched non-US IMGs in IM averaged 248 (2024) {L('NRMP_CO24','NRMP')}; a 2026 median of 251 is quoted elsewhere but not independently verified.""")],
)

G["usce"] = dict(
    mistakes=[
        f"Waiting to book the B1/B2 interview until an observership is confirmed — waits were 6–12 months {L('TRAVELMAN','TravelManToday')}.",
        f"Paying UCLA or Cleveland Clinic for Match purposes — no letters / excludes residency-seekers {L('UCLA_OBS','UCLA')} {L('CCF','Cleveland Clinic')}.",
        f"Accepting “hands-on” work as a graduate on a B visa {L('TEMPLE','Temple ISSS')}.",
        "Booking 2-week blocks with rotating attendings, which rarely produce a detailed letter.",
        f"Applying to SUNY Upstate before you hold the ECFMG certificate {L('UPSTATE','Upstate')}.",
        f"Inconsistent visa applications or social media {L('AAIM_INTEALTH','AAIM')}.",
    ],
    sections=[("Graduate-eligible observerships (verified 15 Nov 2026)",
               f"""| Program | Eligibility | Cost | Notes |
|---|---|---|---|
| UAB (Alabama) {L('UAB','UAB')} | MBBS within 5 years; Step 1 passed | $350 + $4,250 per 4 weeks | Apply 3 months ahead; best first target |
| KU Medical Center {L('KUMC','KUMC')} | IMGs; students ineligible | $50 inquiry + $3,000/month | Windows: 15 Oct–1 Dec 2026; 15 Jan–15 Mar 2027 |
| UT Health San Antonio Hospital Medicine {L('UTHSCSA','UTHSCSA')} | Step 1; LOR; **B-1 before applying** | ~$2,450 per 4 weeks + $10 badge | Cheapest; 60 days ahead |
| Missouri Neurology {L('MU_NEURO','MU')} | Step 1 + 2 CK; 3 LORs; MU sponsor | $750 + $1,800 per 4 weeks | No July starts |
| SUNY Upstate Medicine {L('UPSTATE','Upstate')} | ≥200 first attempt each Step; **ECFMG certificate** | $300 + $1,500 per 2 weeks | No July starts |
| Johns Hopkins Hospital Medicine {L('HOPKINS','Hopkins')} | Built for IMGs | $150 + $9,000–10,000 per 6 weeks | Letter on request |
| Mount Sinai Miami Beach {L('MSMC_MIAMI','MSMC')} | ≥220 on every Step | Fee not listed | 90 days ahead |
| Stanford (department-level) {L('STANFORD_OBGYN','Stanford OB/GYN')} {L('SHC_TV','Tri-Valley')} | Host/approval; 30 days per 12 months | Not published | Bay Area networking |
| UCLA {L('UCLA_OBS','UCLA')} / Cleveland Clinic {L('CCF','CCF')} | No letters / excludes residency-seekers | $750 / $500 | Avoid |

Rough 8-week cost: UTHSCSA ~$4,900; Missouri ~$4,350; Upstate ~$6,300; UAB $8,850; Hopkins $9–10k for 6 weeks, plus flights, housing, visa and insurance."""),
              ("B1/B2 interview waits in India (17 Sep 2026)",
               f"""| Post | Average wait | Next slot |
|---|---|---|
| Hyderabad | 11.5 months | 12 |
| Mumbai | 11 | 11.5 |
| New Delhi | 7.5 | 10 |
| Chennai | 7 | 7.5 |
| Kolkata | 6 | 6.5 |

Secondary source citing State Dept {L('TRAVELMAN','TravelManToday')}; check live at {L('STATE_WAIT','travel.state.gov')}.""")],
)

G["specialty_choice"] = dict(
    mistakes=[
        f"Applying to too few programs — matched IMGs ranked ~2.5× as many {L('NRMP_CO24','NRMP 2024')}.",
        f"Adding a parallel specialty without specialty-specific letters {L('NRMP_PD26','NRMP 2026')}.",
        f"Ignoring year-of-graduation cutoffs {L('FREIDA_HIGHLAND','FREIDA')}.",
        f"Chasing reach fields where non-US IMGs hold <8% of seats {L('NRMP_2026_DATA','NRMP 2026')}.",
    ],
    sections=[("Specialty table for non-US IMGs",
               f"""Score figures: 2024 matched vs unmatched mean {L('NRMP_CO24','NRMP 2024')}; bracketed 2026 median [IQR] quoted elsewhere, **not independently verified**. Seat share: 2026 PGY-1 positions filled by non-US IMGs {L('NRMP_2026_DATA','NRMP 2026')}.

| Specialty | IMG-friendliness | Step 2 CK (matched vs unmatched) | Non-US IMG seat share | Visa | Fit |
|---|---|---|---|---|---|
| Internal Medicine (categorical) | High; 3,448 seats | 248 vs 242 [251; 240–259] | 30.8% | J-1 widely; H-1B minority | **Primary** |
| Family Medicine | High; least score-driven | 231 vs 227 [230; 222–239] | 17.5% | Many community programs no visa | **Parallel safety** |
| Pediatrics | High; 77.6% matched to preferred (2024) | 240 vs 230 [240; 232–252] | 21.9% | J-1 common | Parallel if genuine |
| Psychiatry | Moderate, rising | 240 vs 233 [241; 229–255] | 8.8% | Mixed | Parallel if genuine |
| Neurology | Moderate–high | 245 vs 240 [252; 240–262] | 17.4% | J-1 common | Parallel; 8 signals |
| Pathology | High; ≥71% of programs often interview non-US IMGs | 240 vs 235 [244; 232–253] | 26.7% | J-1 common | Parallel |
| Anesthesiology | Low | 248 vs 245 [255] | 4.4% | Limited | Not this cycle |
| General Surgery | Low (26.6% matched) | 249 vs 244 [256] | 7.1% | Limited | No |
| OB-GYN / EM / PM&R | Low | 239–251 | 2–3% | Limited | No |"""),
              ("Naadi match-chance estimates (judgment, not NRMP figures)",
               "Assumes first-attempt passes, 4–8 weeks of observership with US letters, recent graduation and broad applications.\n\n| Step 2 CK | IM match chance | Strategy |\n|---|---|---|\n| ~260+ | ~80–90% | IM-focused |\n| ~250 | ~65–80% | IM-focused, FM backup |\n| ~240 | ~45–60% (60–70% with FM) | IM + FM in parallel |\n| <235 | ~25–40% | FM-heavy |\n\nObservership-only exposure or graduation 4–5 years back lowers each band ~10–15 points; any USMLE fail lowers it substantially.")],
)

G["eras_application"] = dict(
    mistakes=[
        f"Submitting after programs start reviewing (2027 cycle: 23 Sep) {L('AAMC_ERAS_TL','AAMC')}.",
        "Spending gold signals on UCSF or Stanford.",
        f"Not checking J-1 and graduation-year rules per program in FREIDA {L('FREIDA_HIGHLAND','FREIDA')}.",
        f"Buying a bundled consulting package with no refund policy {L('BBB_MAR','BBB')}.",
    ],
    sections=[("ERAS fee arithmetic",
               f"$11 per program for the first 30, $30 each from the 31st, reset per specialty {L('LECTURIO_ERAS','Lecturio')}.\n\n| Plan | Cost |\n|---|---|\n| 100 IM | 30×$11 + 70×$30 = $2,430 |\n| 150 IM | $3,930 |\n| 30 FM | $330 |\n| ERAS token | $165 (verify; some say $185) {L('IMGPREP','IMGPrep')} |"),
              ("2027 program signals",
               f"| Specialty | Signals |\n|---|---|\n| Internal Medicine (categorical) | 3 gold + 12 silver |\n| Family Medicine | 5 |\n| Pediatrics | 5 |\n| Psychiatry | 10 |\n| Neurology | 8 |\n| Pathology | 5 |\n| Anesthesiology | 5 gold + 10 silver |\n| General Surgery | 15 |\n\nSource: {L('AAMC_SIGNAL','AAMC')}"),
              ("Calendar: 2027 cycle (published) vs 2028 (inferred)",
               f"| Event | 2027 cycle | 2028 cycle |\n|---|---|---|\n| MyERAS opens | 4 Jun 2026 | ~early Jun 2027 (inferred) |\n| Applicants submit | from 2 Sep 2026 | ~1–2 Sep 2027 (inferred) |\n| Programs review | from 23 Sep 2026 | ~late Sep 2027 (inferred) |\n| NRMP registration opens | 15 Sep 2026 | ~mid-Sep 2027 (inferred) |\n\nSources: {L('AAMC_ERAS_TL','AAMC')} {L('NRMP_CAL','NRMP')}")],
)

G["interviews_ranking"] = dict(
    mistakes=[
        f"Ranking too few programs {L('NRMP_CO24','NRMP 2024')}.",
        f"Ranking an H-1B-only program without confirming the fee and Step 3 position {L('FSMB_ATTY','FSMB/attorneys')}.",
        f"Missing that both Step passes and an unexpired Pathway must exist by the ROL deadline {L('PATHWAYS27','ECFMG')} {L('PW_EXP','ECFMG')}.",
    ],
    sections=[("2027 Main Match calendar",
               f"| Event | Date |\n|---|---|\n| Registration opens | 15 Sep 2026 |\n| Standard registration deadline | 29 Jan 2027 ($50 late fee after) |\n| Ranking opens | 1 Feb 2027 |\n| ROL deadline | 3 Mar 2027, 9 PM EST |\n| Match status / SOAP | 15 Mar / 15–18 Mar 2027 |\n| Match Day | 19 Mar 2027 |\n\nSource: {L('NRMP_CAL','NRMP')}")],
)

G["match_day"] = dict(
    mistakes=[
        "Waiting until April to start visa paperwork.",
        f"Accepting a SOAP offer without checking J-1 sponsorship.",
        f"Not telling the program early about a late visa interview {L('EVSP_PRE','Intealth')}.",
    ],
    sections=[("The post-Match critical path",
               f"Contract → MoHFW Statement of Need (₹10 lakh bond, two sureties) {L('MOHFW_OM','MoHFW')} → Intealth J-1 application (TPL-initiated) → DS-2019 in MyIntealth → SEVIS I-901 ($220) → DS-160 ($185) → J-1 interview in India → possible administrative processing → enter no earlier than 30 days before start {L('EVSP_PRE','Intealth')}. Allow 6–10 weeks plus a cushion (inference).")],
)

G["visa_j1"] = dict(
    mistakes=[
        f"A SoN that does not name the exact training specialty {L('EVSP_GEN','ECFMG')}.",
        "Leaving surety arrangements until after the Match.",
        f"Expecting India to issue a NORI {L('CGISF','CGI SF')}.",
        f"Assuming the $100k H-1B fee is gone for good {L('FORBES','Forbes')}.",
        f"Inconsistent DS-160 and social-media history {L('BIZTODAY','Business Today')}.",
    ],
    sections=[("2026 policy watch", POLICY_WATCH),
              ("H-1B $100,000 fee: litigation timeline",
               f"| Date | Event | Source |\n|---|---|---|\n| 19 Sep 2025 | Proclamation 10973 imposes $100k on new H-1B petitions (from 21 Sep 2025) | {L('FSMB_ATTY','FSMB/attorneys')} |\n| 23 Dec 2025 | A federal judge upheld the fee | {L('AAMC_H1B','AAMC')} |\n| 8 Jun 2026 | D. Mass. vacates the policy in full (20-state suit) | {L('CLARKHILL','Clark Hill')} |\n| 24 Jul 2026 | First Circuit denies government's stay | {L('CLARKHILL','Clark Hill')} |\n| Aug 2026 | DHS proposes $103,265 fee rule (not final) | {L('FORBES','Forbes')} |\n| 18 Sep 2026 | Proclamation renewed to 21 Sep 2027 | {L('KLASKO','Klasko')} |\n| 30 Sep 2026 | N.D. Cal. vacates and enjoins the renewal | {L('FORBES','Forbes')} |\n| Nov 2026 | Fee not being collected; litigation continues | {L('FORBES','Forbes')} |"),
              ("Best / typical / worst visa scenarios", SCENARIOS)],
)

G["pre_departure"] = dict(
    mistakes=[
        f"Insurance that starts after the DS-2019 start date or has a gap {L('EVSP_PRE','Intealth')}.",
        f"Arriving more than 30 days before the DS-2019 start {L('EVSP_PRE','Intealth')}.",
        f"Missing an Illinois or Texas training-license deadline — training cannot start without it {L('RUSH','Rush GME')} {L('TMB','TMB')}.",
        f"Packing original documents in checked baggage {L('EVSP_TRAVEL','Intealth')}.",
        f"Remitting large sums as “other purpose” without planning for 20% TCS {L('ICICI_LRS','ICICI')}.",
    ],
    sections=[("State training licenses",
               f"| State | Credential | Fee | Processing | Key points |\n|---|---|---|---|---|\n| New York | None for ACGME residents | Limited permit $105 if needed | — | Full IMG license: 3 yrs GME, Steps 1–3, $735 {L('NYSED','NYSED')} |\n| Texas | Physician-in-Training permit | $200 + $7 | 4-week minimum review; ~14 business days simple; 90–120 days with formal review | Needs TMB ID# from GME; no outside moonlighting {L('TMB','TMB')} |\n| Illinois | Temporary medical license | $230 | 6–8 weeks | Rush 2026 deadline 8 Apr; needs ED-NON form, ECFMG certificate, notarized translations {L('RUSH','Rush GME (institutional)')} |\n| California | PTL | Not re-verified | Within 180 days of start | {L('MBC','MBC')} |"),
              ("Hand-carry list",
               f"Passport · J-1 visa · signed DS-2019 · I-901 receipt · contract or offer letter · insurance proof {L('EVSP_TRAVEL','Intealth')}; plus original MBBS degree, transcripts, ECFMG certificate, Step score reports and vaccination/TB records (inference).")],
)

G["arrival_first_30_days"] = dict(
    mistakes=[
        f"Not checking the I-94 for “J-1 D/S” at the airport {L('EVSP_PRE','Intealth')}.",
        f"Applying for the SSN before SEVIS validation has settled {L('EVSP_PRE','Intealth')}.",
        f"Bringing photocopies to SSA — originals only {L('SSA_PUB','SSA')}.",
        f"Letting a J-2 spouse work before the EAD arrives {L('MIT_J2','MIT')}.",
        f"Paying for “tradeline” credit boosts {L('CAPONE','Capital One')}.",
        f"Claiming India treaty Article 22 without professional advice {L('SPRINTAX','Sprintax')}.",
    ],
    sections=[("First 30 days at a glance",
               "| Day | Action |\n|---|---|\n| 0 | I-94 check at CBP; print I-94 |\n| 1 | Report to program; TPL submits arrival in MyIntealth |\n| ≤10 | Address in MyIntealth |\n| ~15 (11 business days after validation) | SSN application (free) |\n| ~20 | Bank account; secured card after SSN |\n| ≤30 | Driver license; W-4 and nonresident tax setup; J-2 EAD filed |"),
              ("Fixed admission-period rule (watch)",
               f"Published 17 Jul 2026; would replace D/S with an Admit Until Date (program end + 30 days, max 4 years per admission); postponed with no new date as of 23 Sep 2026 {L('UTHSCSA_DS','UTHSCSA OIS')} {L('KLASKO_DS','Klasko')}.")],
)

G["residency_life"] = dict(
    mistakes=[
        f"Moonlighting on a J-1 {L('EVSP_GEN','ECFMG')}.",
        f"Booking a trip home without a valid travel signature or visa stamp {L('EVSP_TRAVEL','Intealth')}.",
        f"Forgetting Form 8843 and the 15 April filing {L('IRS_P519','IRS')}.",
        f"Forgetting MyIntealth address updates within 10 days {L('EVSP_PRE','Intealth')}.",
    ],
    sections=[("PGY-1 pay by region (AAMC, stipends as of 1 Jul 2025)",
               f"| Region | PGY-1 average |\n|---|---|\n| National (unweighted) | $68,166 |\n| Northeast | $74,994 |\n| Southern | $65,076 |\n| Central | $68,580 |\n| **Western** | **$77,649** |\n\nSource: {L('AMA_PAY','AMA')}"),
              ("Bay Area PGY salaries", BAY_PAY)],
)

G["step3_and_licensure"] = dict(
    mistakes=[
        f"Missing California's 180-day PTL deadline — removal from clinical duties {L('UCDAVIS_GME','UC Davis GME')}.",
        f"Exceeding four Step 3 attempts if you want a California license {L('CA_BP2177','B&P 2177')}.",
        f"Banking on state alternative pathways {L('FSMB_CHART','FSMB')}.",
    ],
    sections=[("California licensure summary",
               f"PTL within 180 days of start; PTL valid 36 months; full Physician's and Surgeon's license after 24 months of approved training for IMGs; Step 3 required {L('MBC','MBC')} {L('UCDAVIS_GME','UC Davis GME')}. Step 3 must be passed within 4 attempts {L('CA_BP2177','B&P 2177')}. AB 2386 died 13 Aug 2026 {L('CALMATTERS','CalMatters')}.")],
)

G["post_residency"] = dict(
    mistakes=[
        "Starting the waiver job search in the final months of residency.",
        f"Relying on HHS processing speed {L('KFF','KFF Health News')}.",
        f"Travelling while a J-1→H-1B change of status is pending {L('EVSP_PRE','Intealth')}.",
        f"Assuming a physician NIW makes the India green-card wait short {L('F1JOBS','F1Jobs')}.",
    ],
    sections=[("India employment-based green-card queue (Nov 2026 bulletin, unofficial)",
               f"| Category | Final Action Date | Date for Filing |\n|---|---|---|\n| EB-1 India | 1 Feb 2023 | 1 Jul 2024 |\n| EB-2 India | 1 Nov 2013 | 15 Jan 2015 |\n| EB-3 India | 22 Jan 2014 | 15 Jan 2015 |\n\n356,360 approved EB-2 India petitions were awaiting numbers as of June 2026. Source: {L('F1JOBS','F1Jobs')} — verify on travel.state.gov."),
              ("Best / typical / worst scenarios", SCENARIOS),
              ("2026 policy watch", POLICY_WATCH)],
)

G["california_bay_area"] = dict(
    mistakes=[
        "Building the residency list around Bay Area programs that take almost no non-US IMGs.",
        f"Assuming a Bay Area urban hospital qualifies for Conrad 30 {L('HCAI','HCAI')}.",
        f"Treating HUD FMRs as listing prices — verify and add a margin {L('STARTBIZ','HUD via StartBusinessByState')}.",
    ],
    sections=[("First-year budget: Bay Area vs mid-cost city", FIRST_YEAR),
              ("Bay Area PGY salaries", BAY_PAY),
              ("HUD Fair Market Rents (verify)", HUD),
              ("Best / typical / worst scenarios", SCENARIOS)],
)

OVERVIEW = dict(budget=BUDGET_TO_MATCH, timeline=TIMELINE_2028, policy=POLICY_WATCH, scenarios=SCENARIOS)
