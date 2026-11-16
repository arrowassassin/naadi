from urls import L

TASKS = {}

TASKS["residency_life"] = ("res", [
    ("Know your duty-hour limits",
     f"ACGME limits: at most 80 hours a week averaged over 4 weeks, 24 continuous hours plus up to 4 for transitions, one day in seven free, and in-house call no more often than every third night {L('ACGME_2017','ACGME')}. "
     "Specialties may set stricter limits.",
     "residency_start", 0, "ACGME_2017"),
    ("Do not moonlight on a J-1",
     f"Unauthorized employment, including moonlighting, is prohibited for ECFMG J-1 physicians {L('EVSP_GEN','ECFMG')}. "
     f"Texas PIT permits also bar outside moonlighting {L('TMB','TMB')}.",
     "residency_start", 0, "EVSP_GEN"),
    ("Benchmark your pay",
     f"AAMC's 2025 survey put the national PGY-1 mean at $68,166; regional averages were Northeast $74,994, Southern $65,076, Central $68,580 and Western $77,649 {L('AMA_PAY','AMA')}. "
     "Use this to sanity-check your budget and offers.",
     "residency_start", 0, "AMA_PAY"),
    ("Get a travel signature before any trip abroad",
     f"Re-entry requires a DS-2019 with a travel signature, valid one year or until the DS-2019 end date, whichever comes first; only Intealth Regional Advisors can sign {L('EVSP_TRAVEL','Intealth')}. "
     f"If you lack one, request a duplicate DS-2019, processed within 5 business days in MyIntealth, before booking a visa appointment {L('EVSP_TRAVEL','Intealth')}.",
     "residency_start+150d", 0, "EVSP_TRAVEL"),
    ("Plan any visa renewal in India months ahead",
     f"A renewal abroad needs the DS-160, fees, photo, passport, a paper travel-validated DS-2019 and evidence of nonimmigrant intent; State Department security checks take about 5 business days to 3+ months {L('EVSP_TRAVEL','Intealth')}. "
     f"Re-entry needs a valid J-1 stamp {L('EVSP_PRE','Intealth')}; avoid casual trips home if your stamp has expired (inference).",
     "residency_start+180d", None, "EVSP_TRAVEL"),
    ("Know automatic revalidation for short trips",
     f"Automatic revalidation covers trips of under 30 days to Canada, Mexico or adjacent islands, but not if a new visa application is pending or was denied there {L('EVSP_TRAVEL','Intealth')}. "
     f"Avoid any travel while a change of status is pending {L('EVSP_PRE','Intealth')}.",
     "residency_start+180d", 0, "EVSP_TRAVEL"),
    ("File your US tax return by 15 April",
     f"File a federal return by 15 April even with no US income {L('EVSP_PRE','Intealth')}. "
     f"Attach Form 8843 to Form 1040-NR for each exempt year, or mail it alone to the IRS in Austin, TX 73301-0215 {L('IRS_P519','IRS Pub 519')}.",
     "annual:04-15", None, "IRS_P519"),
    ("Expect FICA to start after two calendar years",
     f"After the first 2 calendar years, J-1 physicians generally become resident aliens for tax purposes and lose the FICA exemption {L('IRS_FICA','IRS')}. "
     "Adjust your budget for the 7.65% deduction from that point (inference on timing).",
     "residency_start+550d", 0, "IRS_FICA"),
    ("Start retirement saving",
     f"The 2026 403(b) elective deferral limit is $24,500, and 457 plans are a separate bucket outside the combined 403(b)/401(k) limit {L('IRS_403B','IRS')}. "
     "The 457(b) limit itself was not confirmed in the sources (verify).",
     "residency_start+60d", 0, "IRS_403B"),
    ("Renew the J-2 EAD on time",
     f"A J-2 EAD renewal can be filed only after the J-1's DS-2019 is extended {L('MIT_J2','MIT ISchO')}. "
     f"Since 12 Dec 2025 USCIS no longer accepts self-submitted photos, so biometrics may be requested {L('MIT_J2','MIT ISchO')}.",
     "residency_start+300d", 520, "MIT_J2"),
    ("Save the Physician Support Line",
     f"The Physician Support Line, 1-888-409-0141, is free, confidential and staffed by volunteer psychiatrists for US physicians; it is not a crisis line, so in a crisis call or text 988 or 911 {L('PSL','Physician Support Line')}.",
     "residency_start", 0, "PSL"),
])

TASKS["step3_and_licensure"] = ("s3lic", [
    ("Check Step 3 eligibility",
     f"IMGs need Step 1 and Step 2 CK passes plus ECFMG certification with a valid, unexpired certificate; completing or nearing one US postgraduate year is recommended, not required {L('USMLE_ELIG','USMLE')}. "
     f"Step 3 is offered only in the US and territories {L('CAREERS360','Careers360')}. The fee (about $955) is unverified.",
     "residency_start+120d", 955, "USMLE_ELIG"),
    ("Schedule Step 3 during PGY-1",
     f"The 2025 IMG first-time Step 3 pass rate was 88% {L('USMLE_PERF','USMLE')}. "
     f"California requires Step 3 for the full license {L('UCDAVIS_GME','UC Davis GME')}, and an H-1B for post-waiver work generally requires it (inference). PGY-1 is the recommended window.",
     "residency_start+240d", 0, "USMLE_ELIG"),
    ("H-1B strategy only: consider Step 3 before ERAS",
     f"An H-1B for residency generally needs Step 3 passed before the petition {L('FSMB_ATTY','FSMB/attorneys')}. "
     "Naadi's judgment: attempt it before ERAS only if your Step 2 CK is about 250+, you are ECFMG-certified by mid-year, and you have named H-1B programs you would rank; it costs about $955 (unverified) plus a US stay.",
     "eras_submit-60d", 955, "FSMB_ATTY"),
    ("Respect California's four-attempt Step 3 rule",
     f"California requires passing all parts of Step 3 “within not more than four attempts”; applicants who needed more may still be considered under B&P §2135 or §2135.5 {L('CA_BP2177','B&P 2177')}.",
     "residency_start+240d", 0, "CA_BP2177"),
    ("California: obtain your PTL within 180 days",
     f"California ACGME trainees must obtain a Postgraduate Training License within 180 days of their program start date {L('MBC','MBC')}. "
     f"The PTL is valid 36 months; deadlines are statutory, “not flexible”, and missing one removes you from clinical duties {L('UCDAVIS_GME','UC Davis GME')}. The PTL fee was not re-verified.",
     "residency_start+150d", None, "MBC"),
    ("California: apply for the full P&S license after 24 months",
     f"IMGs can move from the PTL to a full Physician's and Surgeon's license after 24 months of Board-approved training (12 for US/Canadian graduates), and Step 3 is required {L('MBC','MBC')} {L('UCDAVIS_GME','UC Davis GME')}.",
     "residency_start+700d", None, "MBC"),
    ("New York: know the full-license bar",
     f"A full New York license for IMGs needs 3 years of accredited GME, Steps 1–3, a $735 fee and FCVS {L('NYSED','NYSED')}. "
     f"A limited permit ($105) is needed only outside ACGME residency, valid 2 years and renewable once {L('NYSED','NYSED')}.",
     "residency_start+900d", 735, "NYSED"),
    ("Permanently validate your Pathway-based ECFMG certificate",
     f"A Pathway-based certificate expires with its Pathway unless revalidated or permanently validated; 12 months of ACGME-accredited training permanently validates it {L('PW_EXP_OLD','ECFMG')}. "
     "Confirm validation shows in MyIntealth after PGY-1.",
     "residency_start+380d", 0, "PW_EXP_OLD"),
    ("Do not count on state alternative licensure pathways",
     f"California's AB 2386 died in Senate Appropriations on 13 Aug 2026 {L('CALMATTERS','CalMatters')}. "
     f"The pathways enacted elsewhere (18 states per FSMB's Oct 2025 chart) require prior licensed practice or postgraduate training abroad, so none help a fresh MBBS graduate {L('FSMB_CHART','FSMB')} {L('WES','WES')}.",
     "residency_start", 0, "FSMB_CHART"),
])

TASKS["post_residency"] = ("post", [
    ("Decide on fellowship in PGY-2",
     f"In the 2027 ERAS Fellowships season, the July cycle opened to applicants on 1 Jul 2026 with program review from 15 Jul; the December cycle opened 12 Nov 2026 {L('AAMC_FELLOW','AAMC')}. "
     "IM subspecialties use the July cycle, so a July-start resident applies early in PGY-3 and prepares in PGY-2 (inference).",
     "residency_start+400d", None, "AAMC_FELLOW"),
    ("Know the Medicine Specialties Match calendar",
     f"In the fall 2026 Medicine & Pediatric Specialties Match, rank-list certification was due 18 Nov 2026, 9 p.m. ET, and Match Day was 2 Dec 2026 {L('NRMP_ILLUM','NRMP')}. "
     "Expect a similar November–December pattern in your fellowship year.",
     "residency_start+850d", None, "NRMP_ILLUM"),
    ("Check J-1 time left before choosing fellowship",
     f"ECFMG J-1 sponsorship is generally limited to seven years and the SoN must name the exact training specialty {L('EVSP_GEN','ECFMG EVSP')}. "
     f"Programs longer than 7 years in total need an Exceptional Need Certificate from MoHFW {L('MOHFW_OM','MoHFW')}.",
     "residency_start+400d", 0, "EVSP_GEN"),
    ("Confirm board eligibility",
     f"ABIM eligibility requires completed accredited training plus a full, unrestricted state medical license in good standing {L('ABIM','ABIM')}. "
     "Plan the full license (see Step 3 and licensure) well before your final year ends.",
     "residency_end-180d", None, "ABIM"),
    ("Choose: waiver job or two years in India",
     f"J-1 physicians must complete two years' physical presence in the home country before certain status changes unless waived {L('EVSP_GEN','ECFMG')}. "
     f"India will not issue a NORI {L('CGISF','CGI SF')}, so a Conrad 30, HHS or similar waiver is the route to stay; without one you must leave and wait two years before an H-1B {L('KFF','KFF Health News')}.",
     "residency_end-540d", 0, "USCIS_CONRAD"),
    ("Start the waiver job search in PGY-2",
     f"Conrad 30 needs a full-time (40 h/week) H-1B contract for at least 3 years at a HPSA, MUA or MUP facility, starting within 90 days of the waiver {L('USCIS_CONRAD','USCIS')}. "
     f"3RNet publishes a J-1 waiver job-search timeline, but the page was login-gated and its contents are unverified {L('THREERNET','3RNet')}.",
     "residency_end-540d", 0, "USCIS_CONRAD"),
    ("California Conrad 30: apply 1–31 October",
     f"HCAI's 2026–27 window was 1–31 Oct 2026, with a secondary 1–31 Dec cycle if slots remain; applications are reviewed in order of receipt with rural priority and no FLEX slots {L('HCAI','HCAI')}. "
     f"Practice must be full-time in a HPSA, MUA or MUP (MHPSA for psychiatry) {L('HCAI','HCAI')}.",
     "residency_end-270d", None, "HCAI"),
    ("Apply in more than one state and do not rely on HHS speed",
     f"The HHS clinical waiver stopped in late 2025 and resumed “dramatically slower”, leaving hundreds backlogged vs 1–3 weeks historically {L('KFF','KFF Health News')}. "
     f"A June 2026 Senate letter described an “administrative bottleneck” with no HHS timeline {L('GILLIBRAND','Sen. Gillibrand')}. "
     f"California also issues HHS support letters year-round for primary care (FM, general IM, peds, OB/GYN, psychiatry) {L('HCAI','HCAI')}.",
     "residency_end-300d", None, "HCAI"),
    ("Change status from J-1 to H-1B for the waiver job",
     f"The H-1B cap does not apply to physicians changing status under a state- or federal-agency-requested waiver {L('LII_1184','8 USC 1184')}. "
     f"The $100,000 proclamation does not apply to a J-1 to H-1B change of status {L('FSMB_ATTY','FSMB/attorneys')}. Avoid travel while the change is pending {L('EVSP_PRE','Intealth')}.",
     "residency_end-90d", None, "LII_1184"),
    ("Watch the 30-day grace period",
     f"After training ends you have 30 days to depart, with no work or training authorization during that time {L('EVSP_GEN','ECFMG EVSP')}. "
     f"In 2026, doctors waiting on HHS waivers likely had to reach USCIS by 30 July or leave {L('KFF','KFF Health News')}.",
     "residency_end", 0, "EVSP_GEN"),
    ("Start the green-card plan early",
     f"Nov 2026 Visa Bulletin (India): EB-1 1 Feb 2023, EB-2 1 Nov 2013, EB-3 22 Jan 2014; 356,360 approved EB-2 India petitions were waiting as of June 2026 (unofficial; verify on travel.state.gov) {L('F1JOBS','F1Jobs')}. "
     f"EB-2 India Final Action was “unavailable” in the July 2026 bulletin {L('NATLAW','National Law Review')}. "
     "Filing an I-140 early to lock a priority date is advised (inference); EB-1 or a US-citizen/green-card spouse are the faster routes.",
     "residency_end", None, "F1JOBS"),
    ("Know cap-subject vs cap-exempt employers",
     f"Cap exemptions cover institutions of higher education, related nonprofits and nonprofit or government research organizations {L('LII_1184','8 USC 1184')} {L('OGLETREE_CAPEX','Ogletree')}. "
     "Community hospitals not affiliated with a university are usually cap-subject (inference).",
     "residency_end-365d", 0, "OGLETREE_CAPEX"),
    ("H-4 spouse work rules if you move to H-1B",
     f"An H-4 spouse can get an EAD only if the H-1B principal has an approved I-140 or AC21 extensions; renewal can be filed up to 180 days ahead {L('USCIS_H4','USCIS')}.",
     "residency_end-60d", None, "USCIS_H4"),
    ("Redesignate Indian bank accounts",
     f"On becoming non-resident, existing resident accounts should be redesignated NRO; NRE accounts are repatriable and their interest is exempt from income tax {L('RBI_FAQ','RBI')}. "
     f"NRIs cannot remit under LRS {L('ICICI_LRS','ICICI')}.",
     "residency_end-30d", 0, "RBI_FAQ"),
    ("Brief your sureties if you will not return",
     f"The MoHFW bond and undertaking carry a ₹10 lakh demand plus 12% interest on default {L('MOHFW_OM','MoHFW')}. "
     "How MoHFW enforces it against waiver recipients is unknown, so tell your sureties plainly that their exposure is real.",
     "residency_end-365d", 0, "MOHFW_OM"),
    ("Get a personal DEA registration for attending practice",
     f"During residency you can prescribe under the hospital's DEA number with a suffix {L('LII_DEA','21 CFR 1301.22')}; a personal registration matters mainly afterwards (inference). "
     "Apply once your full state license is issued.",
     "residency_end-60d", None, "LII_DEA"),
])

TASKS["california_bay_area"] = ("ca", [
    ("Build a realistic California residency list",
     f"High-IMG California IM programs (nearly all J-1) include San Joaquin General, Adventist Health Tulare, Prime West sites, St Agnes Fresno, Kern Medical, UCR/St Bernardine and UCSF-Fresno {L('RMAI_CA','ResidencyMatch.ai, secondary')}. "
     f"Highland in Oakland is a stretch: 577 interviews for 30 PGY-1 seats {L('FREIDA_HIGHLAND','FREIDA')}.",
     "eras_submit-60d", 0, "RMAI_CA"),
    ("Treat Bay Area academic and Kaiser programs as reaches",
     f"Kaiser NorCal Oakland and San Francisco IM show about 0% IMGs and Santa Clara Valley about 3% {L('RMAI_CA','ResidencyMatch.ai')}. "
     f"SCVMC's IM application page lists no visa, graduation-year or score information {L('SCVMC','SCVMC')}.",
     "eras_submit-60d", 0, "RMAI_CA"),
    ("Check Kern and UCSF-Fresno requirements",
     f"Kern Medical IM is J-1 only, needs the ECFMG certificate and Steps 1–2, and asks for ERAS signals instead of emailed letters of interest {L('KERN','Kern')}. "
     f"UCSF-Fresno IM is J-1 only and considers IMGs less than 5 years from graduation {L('UCSF_FRESNO','UCSF Fresno')}.",
     "eras_submit-45d", 0, "KERN"),
    ("Network through Stanford's visiting-observer programs",
     f"Stanford OB/GYN accepts international physicians for up to 30 days in 12 months with approval taking up to 6 weeks; no visa or housing is provided {L('STANFORD_OBGYN','Stanford')}. "
     f"Stanford Health Care Tri-Valley requires a host with privileges there, filing at least 3 weeks ahead {L('SHC_TV','SHC Tri-Valley')}. Fees and letter policies are unpublished.",
     "observership_start-90d", None, "STANFORD_OBGYN"),
    ("Compare Bay Area resident pay",
     f"2026–27 PGY-1: UCSF $101,801 {L('UCSF_SAL','UCSF')}; Stanford $96,948.80 (pending contract) {L('STANFORD_SAL','Stanford')}; Kaiser NorCal Oakland IM $97,939 {L('FREIDA_KAISER','FREIDA')}; Highland IM $83,905 {L('FREIDA_HIGHLAND_BEN','FREIDA')}. "
     f"All are well above the AAMC Western PGY-1 average of $77,649 {L('AMA_PAY','AMA')}.",
     "match_day+7d", 0, "UCSF_SAL"),
    ("Check rents against HUD Fair Market Rents",
     f"FY2027 1BR FMRs: San Francisco $3,003, San Jose $2,778, Oakland–Fremont $2,290, Stockton–Lodi $1,628, Fresno $1,363 (secondary transcription; verify at huduser.gov) {L('STARTBIZ','StartBusinessByState')}. "
     "At the Oakland FMR a PGY-1 on $97–102k spends about 27% of gross pay on rent (inference).",
     "match_day+14d", 0, "STARTBIZ"),
    ("Get your California PTL on time",
     f"California IMG residents need a PTL within 180 days of starting, and can get the full license after 24 months of approved training, with Step 3 required {L('MBC','MBC')}. "
     f"Kern requires residents to meet California licensure requirements with no stipulations on Medical Board status {L('KERN','Kern')}.",
     "residency_start+150d", None, "MBC"),
    ("Know that training outside California still works",
     f"Residency in any ACGME-accredited program counts toward the California training requirement; California's license is a separate application you can start in your final year (inference from MBC rules) {L('MBC','MBC')}. "
     "Do not let the Bay Area goal shrink your residency list.",
     "eras_submit-60d", 0, "MBC"),
    ("Target Bay-adjacent shortage-area sites for the waiver",
     f"California's Conrad 30 requires full-time practice in a HPSA, MUA or MUP and gives rural sites priority {L('HCAI','HCAI')}. "
     "Most Bay Area urban hospitals will not qualify; a Bay Area FQHC in a HPSA is possible but not assured (inference).",
     "residency_end-540d", 0, "HCAI"),
    ("Do not wait for a California alternative pathway",
     f"AB 2386, a provisional license for internationally licensed physicians, passed the Assembly but was held in Senate Appropriations on 13 Aug 2026 and never reached the governor {L('CALMATTERS','CalMatters')}. "
     f"The Mexico physicians program re-established by AB 2860 is Mexico-specific {L('FSMB_CHART','FSMB')}.",
     "start", 0, "CALMATTERS"),
    ("Get your California driver license after you settle",
     f"California says all residents need a California license; applicants need an original legal-presence document valid at least 30 days to start and 60 days to receive a photo card {L('CA_DMV_DL','CA DMV')} {L('CA_DMV_LP','CA DMV')}. "
     "An SSN is required only if you are authorized to work, which a J-1 resident is.",
     "arrival+30d", None, "CA_DMV_LP"),
])
