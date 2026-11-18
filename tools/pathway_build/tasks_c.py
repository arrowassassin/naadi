from urls import L

TASKS = {}

TASKS["visa_j1"] = ("visa", [
    ("Apply to MoHFW for the Statement of Need",
     f"The SoN is processed only by MoHFW in New Delhi, not by consulates: Under Secretary (IC), International Cooperation Section, Room 514, 'A' Wing, Nirman Bhavan, New Delhi 110011; soic-mohfw@nic.in; +91-11-2306-3068 / 2306-1945 {L('CGISF','CGI San Francisco')}. "
     "MoHFW's processing time, application fee and document checklist were not found in any source (verify), so apply the week after Match.",
     "match_day+3d", None, "CGISF"),
    ("Make sure the SoN wording is exactly right",
     f"The SoN must be on official letterhead, addressed to ECFMG, use the exact 22 CFR §62.27 wording, name a specialty that exactly matches your training specialty, and carry an official stamp and dated signature {L('EVSP_GEN','ECFMG EVSP')}. "
     f"It must arrive in a sealed Ministry envelope or as a PDF from an official Ministry email {L('EVSP_GEN','ECFMG EVSP')}. A later specialty switch needs a new SoN.",
     "match_day+3d", 0, "EVSP_GEN"),
    ("Arrange the ₹10 lakh surety bond and two sureties",
     f"Each SoN needs a separate surety bond of ₹10 lakh on ₹100 non-judicial stamp paper, with two financially sound sureties: one close relative who is an Indian citizen in India and one unrelated person {L('MOHFW_OM','MoHFW OM 2023')}. "
     "The ₹10 lakh is a contingent liability, not a fee; only the stamp paper (about $1) is a cost.",
     "match_day+5d", 1, "MOHFW_OM"),
    ("Understand the bond's return-to-India clause",
     f"The MoHFW guidelines include a clause to report back to India within 3 months of completing training (extendable by 3 months), with a penalty demand of ₹10 lakh plus 12% annual interest; the scan is partly illegible (verify against the original) {L('MOHFW_OM','MoHFW')}. "
     f"The SoN is capped at a combined 7 years on J-1; longer programs need an Exceptional Need Certificate {L('MOHFW_OM','MoHFW')}. Brief your sureties on their exposure.",
     "match_day+5d", 0, "MOHFW_OM"),
    ("Know the J-1 rules before you sign anything",
     f"ECFMG sponsorship is generally limited to 7 years, authorizes one training activity, location and compensation, allows one specialty change in the first two years, and prohibits moonlighting {L('EVSP_GEN','ECFMG EVSP')}. "
     f"J-1 physicians and J-2 dependents are subject to INA 212(e) {L('EVSP_GEN','ECFMG EVSP')}, and MoHFW will not issue a NORI “under any circumstance” {L('CGISF','CGI SF')}.",
     "match_day+3d", 0, "EVSP_GEN"),
    ("Complete the Intealth J-1 application with your program",
     f"After approval the DS-2019 appears in MyIntealth for you and your TPL {L('EVSP_PRE','Intealth')}. "
     "The current Intealth sponsorship fee is unverified; programs often pay it, so confirm with your GME office (verify).",
     "match_day+14d", None, "EVSP_PRE"),
    ("Print and sign the DS-2019",
     f"Print the DS-2019 single-page, double-sided, and sign it in ink {L('EVSP_PRE','Intealth')}. "
     "Keep the paper original for the interview and for every future entry.",
     "match_day+45d", 0, "EVSP_PRE"),
    ("Pay the SEVIS I-901 fee",
     f"The I-901 fee for J exchange visitors is $220 (full payment), paid at ice.gov/sevis/i901 {L('ICE_I901','ICE')}; J-2 dependents are not charged separately {L('EVSP_PRE','Intealth')}. "
     f"The $220 rate took effect 24 Jun 2019 {L('BAL_I901','BAL')}. Save the receipt.",
     "match_day+46d", 220, "ICE_I901"),
    ("File the DS-160 and pay the $185 visa fee",
     f"Complete the DS-160 and pay the $185 visa fee {L('EVSP_PRE','Intealth')}. "
     f"Since June 2025, F/M/J applicants must set social-media accounts to public and list all usernames from the past 5 years on the DS-160 {L('BIZTODAY','Business Today, secondary')}.",
     "match_day+47d", 185, "EVSP_PRE"),
    ("Book the earliest J-1 interview at any Indian post",
     f"You must interview in India {L('FAEGRE','Faegre Drinker')}. "
     f"In June 2026 Intealth encouraged Indian physicians previously denied an appointment to reapply for a J visa at a post in India, while stressing decisions are at the consular officer's discretion {L('AAIM_INTEALTH','AAIM')}. "
     "Whether physicians are formally prioritized in 2026 is unconfirmed (verify).",
     "match_day+48d", 0, "STATE_WAIT"),
    ("Assemble the interview document set",
     f"Bring a passport valid 6 months beyond the DS-2019 end date, the signed DS-2019, financial evidence (contract or offer letter, funding letter, bank statement), the SEVIS receipt, a 2×2 inch photo and the DS-160 confirmation {L('EVSP_PRE','Intealth')}. "
     f"You must show a permanent residence abroad, strong ties and how the training will be useful at home {L('EVSP_PRE','Intealth')}.",
     "match_day+55d", 0, "EVSP_PRE"),
    ("Prepare to explain earlier B-visa observerships",
     f"Reports in 2026 described matched Indian residents facing visa refusals, prolonged administrative processing or cancelled visitor visas {L('SAH','South Asian Herald')}. "
     f"Intealth has explained to agencies that observerships and J-1 training are distinct {L('AAIM_INTEALTH','AAIM')}. "
     "Be ready to describe past visits as clinical observation only (inference; the cause of cancellations was not confirmed).",
     "match_day+55d", 0, "AAIM_INTEALTH"),
    ("Plan for administrative processing and a late start",
     f"Your visa may go into administrative processing {L('EVSP_PRE','Intealth')}. "
     f"In July 2026 more than 20 medical societies said visa delays were keeping foreign-born residents from 1 July starts {L('CANCERLETTER','The Cancer Letter')}. "
     "If your interview falls after mid-June, tell your program coordinator immediately.",
     "residency_start-45d", 0, "EVSP_PRE"),
    ("Collect your passport",
     f"Since August 2025, passports in New Delhi must be collected in person (secondary) {L('BIZTODAY','Business Today')}. "
     "Check the J-1 annotation and your name against the DS-2019 before leaving the counter.",
     "residency_start-35d", 0, "BIZTODAY"),
    ("Register J-2 dependents correctly",
     f"J-2 dependents are also subject to 212(e) and to the insurance minimums {L('EVSP_GEN','ECFMG')}. "
     f"J-2s are not charged a separate SEVIS fee {L('EVSP_PRE','Intealth')}.",
     "match_day+30d", 0, "EVSP_GEN"),
    ("H-1B branch: confirm Step 3 and the petition timeline",
     f"An H-1B for residency generally needs Step 3 passed before the petition is filed {L('FSMB_ATTY','FSMB/attorneys')}. "
     f"Teaching hospitals tied to universities or nonprofits are usually cap-exempt {L('OGLETREE_CAPEX','Ogletree')}, so the wage-weighted lottery (in effect 27 Feb 2026) rarely applies {L('OGLETREE_LOTTERY','Ogletree')}.",
     "match_day+7d", None, "FSMB_ATTY"),
    ("H-1B branch: check the $100,000 fee status before relying on it",
     f"Proclamation 10973 applies to new petitions for physicians outside the US, which describes you if you match from India; the employer pays {L('FSMB_ATTY','FSMB/attorneys')}. "
     f"Vacated 8 Jun 2026 with a stay denied 24 Jul {L('CLARKHILL','Clark Hill')}, renewed 18 Sep 2026 to 21 Sep 2027 {L('KLASKO','Klasko')}, then vacated and enjoined 30 Sep 2026 {L('FORBES','Forbes')}. "
     "As of Nov 2026 it is not being collected, but litigation and a proposed $103,265 fee rule continue.",
     "match_day+7d", 0, "FORBES"),
    ("H-1B branch: expect longer consular vetting",
     f"Online-presence review was extended to all H-1B/H-4 applicants from 15 Dec 2025, and thousands of Indian H-1B interviews were rescheduled to Mar–May 2026 {L('PIE','The PIE News')} {L('GIB_DEC25','Global Immigration Blog')}. "
     "Book the H-1B stamping appointment the day the I-797 approval arrives.",
     "match_day+60d", None, "PIE"),
])

TASKS["pre_departure"] = ("predep", [
    ("Buy J-1-compliant health insurance",
     f"Coverage must be at least $100,000 per accident or illness, with a deductible of no more than $500 and co-insurance of no more than 25%, for you and any J-2s; ECFMG provides repatriation ($25,000) and medical evacuation ($50,000) cover {L('EVSP_GEN','ECFMG EVSP')}. "
     f"Insurance must start on the DS-2019 program start date and continue without a break {L('EVSP_PRE','Intealth')}. Gap-period cost was not sourced.",
     "residency_start-30d", None, "EVSP_PRE"),
    ("Get the state training license your program needs",
     f"New York: ACGME residents need no license {L('NYSED','NYSED')}. Texas: Physician-in-Training permit, $200 + $7 surcharge, 14 business days for simple files to 90–120 days with formal review, applied for after the GME office gives you a TMB ID {L('TMB','TMB')}. "
     f"Illinois: temporary license $230, 6–8 weeks, and training cannot start until it is issued {L('RUSH','Rush GME')}. California: see the PTL task in Step 3 and licensure.",
     "residency_start-90d", None, "TMB"),
    ("Complete TB screening",
     f"Pre-placement screening for healthcare personnel is a TB risk assessment, a symptom evaluation, and an IGRA or TST if you have no documented prior TB or LTBI; new positives need a chest X-ray {L('CDC_TB','CDC MMWR')}. "
     "BCG, near-universal in India, can cause false-positive TSTs, so bring any prior IGRA (QuantiFERON) report (inference, not sourced).",
     "residency_start-45d", None, "CDC_TB"),
    ("Gather vaccination and onboarding records",
     "Bring records for MMR, varicella, hepatitis B with titers, Tdap, influenza and COVID (inference, not sourced). "
     f"Drug screens, background checks and BLS/ACLS vary by institution and no cross-program official source was found, so follow your GME office's checklist {L('CDC_TB','CDC')}.",
     "residency_start-45d", None, "CDC_TB"),
    ("Apply for your NPI",
     f"The NPI is free; apply online through NPPES in about 20 minutes, and update NPPES within 30 days of any change {L('CMS_NPI','CMS')}. "
     "Programs usually ask for it at onboarding once you have a US address and ideally an SSN (timing unverified).",
     "residency_start-14d", 0, "CMS_NPI"),
    ("Skip a personal DEA registration",
     f"A hospital-employed practitioner may prescribe under the hospital's DEA registration with a hospital-assigned suffix, if the state permits it {L('LII_DEA','21 CFR 1301.22')}. "
     "A personal DEA matters mainly after residency.",
     "residency_start-30d", 0, "LII_DEA"),
    ("Plan transfers from India under LRS",
     f"Residents can remit up to USD 250,000 per financial year; forex-card loads count toward the limit {L('ICICI_LRS','ICICI')}. "
     f"The first ₹10 lakh a year is TCS-exempt; above that TCS is 2% for education or medical purposes and 20% for other purposes, creditable against income tax (rates from 1 Apr 2026) {L('ICICI_LRS','ICICI')}. "
     "Splitting transfers across family members' PANs or across two financial years reduces TCS cash tied up (inference).",
     "residency_start-60d", 0, "ICICI_LRS"),
    ("Know the border cash rule",
     f"You may bring any amount into the US, but more than $10,000 in total (cash, traveler's checks, money orders, bearer instruments) must be reported on FinCEN 105 {L('CBP_MONEY','CBP')}. "
     "India's limit on carrying currency notes out (commonly quoted as USD 3,000) is unverified (verify).",
     "residency_start-14d", 0, "CBP_MONEY"),
    ("Get an Indian International Driving Permit",
     f"An Indian IDP is valid 1 year and costs about ₹1,000–1,500; apply via Parivahan Sarathi (Form 4A) or your RTO, typically within 3–7 days, with your licence, passport, visa, ticket and Form 1A medical certificate (secondary, verify on Parivahan) {L('SPINNY','Spinny')}. "
     "Whether it lets you drive in California, and for how long, is unverified.",
     "residency_start-30d", 15, "SPINNY"),
    ("Book a flight that lands no earlier than 30 days before start",
     f"You may enter the US no earlier than 30 days before the DS-2019 start date {L('EVSP_PRE','Intealth')}. "
     "A planning estimate for the flight is $700–1,200 (unsourced estimate). Leave room for orientation, which often starts before 1 July.",
     "residency_start-30d", None, "EVSP_PRE"),
    ("Line up housing near the hospital",
     f"HUD FY2027 1-bedroom Fair Market Rents range from about $1,050 (Cleveland) to $3,003 (San Francisco) (secondary transcription; verify at huduser.gov) {L('STARTBIZ','StartBusinessByState')}. "
     "Budget a deposit plus first month of about 2–3× monthly rent (unsourced estimate).",
     "residency_start-45d", None, "STARTBIZ"),
    ("Pack a hand-carry document folder",
     f"Hand-carry passport, J-1 visa, signed DS-2019, I-901 receipt, contract or offer letter and insurance proof; do not check documents in baggage {L('EVSP_TRAVEL','Intealth')} {L('EVSP_PRE','Intealth')}. "
     "Also carry original MBBS degree, transcripts, ECFMG certificate, Step score reports and vaccination/TB records (inference).",
     "residency_start-7d", 0, "EVSP_TRAVEL"),
])

TASKS["arrival_first_30_days"] = ("arr", [
    ("Check your I-94 at entry",
     f"At CBP present passport, visa and DS-2019; the I-94 should read “J-1 D/S” {L('EVSP_PRE','Intealth')}. "
     f"If it shows a fixed date or a different status, contact Intealth immediately, and print the electronic I-94 {L('EVSP_PRE','Intealth')}.",
     "arrival", 0, "EVSP_PRE"),
    ("Report to your program for SEVIS validation",
     f"The TPL submits the arrival form plus copies of your visa and I-94 in MyIntealth, and Intealth validates you in SEVIS “as soon as possible” {L('EVSP_PRE','Intealth')}. "
     f"After validation the program start date cannot be amended {L('EVSP_PRE','Intealth')}.",
     "arrival+1d", 0, "EVSP_PRE"),
    ("Report your US address in MyIntealth",
     f"Report address changes within 10 days through MyIntealth {L('EVSP_PRE','Intealth')}. "
     "Do it again every time you move.",
     "arrival+10d", 0, "EVSP_PRE"),
    ("Apply for your Social Security Number",
     f"Intealth says wait 11 business days after SEVIS validation and select “other” for citizenship {L('EVSP_PRE','Intealth')}; SSA says wait 10 days after arrival {L('SSA_PUB','SSA')}. "
     f"Start online, visit an SSA office within 45 days, bring originals of your DS-2019, I-94 and passport (no photocopies); the service is free {L('SSA_PUB','SSA')}. "
     f"USCIS paused Enumeration Beyond Entry in April 2025, so apply directly to SSA {L('MIT_J2','MIT ISchO')}.",
     "arrival+15d", 0, "SSA_PUB"),
    ("Open a bank account and build credit",
     f"Foreign credit history does not transfer; secured cards need a refundable deposit, e.g. Capital One Platinum Secured takes $49, $99 or $200 for a line of at least $200 {L('CAPONE','Capital One')}. "
     f"Issuers usually ask for an SSN, and the FTC warns against paid “tradeline renting” {L('CAPONE','Capital One')}.",
     "arrival+20d", None, "CAPONE"),
    ("Get a state driver license (California example)",
     f"California needs an online application, an office visit (identity, residency documents, thumbprint, vision, photo), a knowledge test and a drive test, 3 attempts each; bring proof of insurance and an accompanying licensed driver if you hold a foreign license {L('CA_DMV_DL','CA DMV')}. "
     f"If your legal presence ends within 5 years you get a limited-term license {L('CA_DMV_LP','CA DMV')}. Plan to get it promptly once you are a resident (inference).",
     "arrival+30d", None, "CA_DMV_DL"),
    ("Set up nonresident tax status and your W-4",
     f"A J-1 “teacher or trainee” is exempt from the substantial-presence count unless exempt for any part of 2 of the 6 preceding calendar years, and files Form 8843 each exempt year {L('IRS_P519','IRS Pub 519')}. "
     "So J-1 physicians are generally nonresident aliens filing 1040-NR for the first 2 calendar years, with the July arrival year counting as year 1 (well-supported inference, verify). "
     "Use a nonresident tax preparer; W-4 nonresident instructions were not sourced.",
     "arrival+14d", None, "IRS_P519"),
    ("Check your payslip for the FICA exemption",
     f"Nonresident aliens in J status are exempt from Social Security and Medicare tax on wages for services that carry out the purpose of admission; the exemption ends when you become a resident alien for tax purposes and does not apply to J-2s {L('IRS_FICA','IRS')} {L('IRS_P519','IRS Pub 519')}. "
     "FICA is 7.65%, roughly $10k over two years on a ~$70k stipend (inference).",
     "arrival+45d", 0, "IRS_FICA"),
    ("Ask a preparer about the India treaty standard deduction",
     f"Under Article 21(2), eligible students and business apprentices from India may claim the standard deduction {L('IRS_P519','IRS Pub 519')}. "
     f"Whether a salaried resident physician counts is unverified, and Article 22 (teachers/researchers) carries a retroactive trap {L('SPRINTAX','Sprintax')}; do not claim either without professional advice.",
     "arrival+30d", None, "SPRINTAX"),
    ("File the J-2 spouse's work permit (EAD)",
     f"File Form I-765, category (c)(5), by mail; the fee is $520 by credit card, processing about 1–5 months, valid 1 year or the DS-2019 period {L('MIT_J2','MIT ISchO')}. "
     f"The spouse may not work until the card arrives, and the income may not support the J-1 holder; a signed statement is required {L('MIT_J2','MIT ISchO')}.",
     "arrival+14d", 520, "MIT_J2"),
    ("Enroll children in public school",
     f"Under Plyler v. Doe, a student's or parent's immigration status cannot be used to bar a child from public school {L('ED_OCR','US Dept of Education')}. "
     "Districts may ask for proof of residency and age.",
     "arrival+14d", 0, "ED_OCR"),
    ("Watch the fixed admission-period (D/S) rule",
     f"A DHS final rule published 17 Jul 2026 replaces D/S with a fixed Admit Until Date (program end plus 30 days, maximum 4 years per admission); extensions would need Form I-539 or travel and re-entry {L('UTHSCSA_DS','UTHSCSA OIS')}. "
     f"Due 15 Sep 2026, it was postponed with no new date as of 23 Sep 2026; physicians already in D/S generally see no immediate change {L('UTHSCSA_DS','UTHSCSA OIS')} {L('KLASKO_DS','Klasko')}.",
     "arrival+30d", 0, "UTHSCSA_DS"),
])
