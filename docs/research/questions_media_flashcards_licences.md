# Research B: practice questions, media, flashcards (licensing)

Fetched 2026-11-17 with `curl -sL` through the session proxy. WebFetch could not resolve any host in this session, so it was not used.

**How to read the evidence labels**
- **[fetched]**: the quote was copied verbatim from the page or PDF at the URL given.
- **[search-only]**: the page could not be fetched (bot block, broken TLS, or a JS challenge). The text comes from a web-search summary and is **not verified**. Re-check it before relying on it.
- **UNKNOWN**: no primary statement was found.

Not legal advice. Several conclusions depend on whether Naadi is "commercial", which is **UNKNOWN** to me (see §0).

---

## 0. Decisions that hinge on "commercial vs non-commercial"

Many sources below are NC (non-commercial): Radiopaedia, UMich Histology, PEIR, WebPath (personal use only), OpenStax (now NC-SA), and the NBME documents. If Naadi has paid tiers, ads, or any revenue, treat every NC source as **link-only** unless you get written permission. The commercially safe stores are:
- Wikimedia Commons files under CC0/PD/CC BY/CC BY-SA (checked per file)
- Servier Medical Art (CC BY 4.0)
- Open-i/PMC figures whose per-article licence is CC BY/CC0
- NLM/CDC US-government works
- MedExpQA (CC BY 4.0)
- PubMedQA/MMLU/HEAD-QA code and annotations (MIT), with caveats on the underlying text

---

## A. Practice questions

### A1. USMLE sample test questions (Step 1, Step 2 CK)

- Step 1 sample page [fetched]: https://www.usmle.org/exam-resources/step-1-materials/step-1-sample-test-questions
  > "More than 100 sample Step 1 test questions are available in both PDF format and as an interactive testing experience."

  Interactive versions: `https://orientation.nbme.org/Launch/USMLE/STPF1` (before May 14) and `https://www.starttest.com/custom/usmle/?stpf1` (new 30-min-block software).
- Step 2 CK sample page [fetched]: https://www.usmle.org/exam-resources/step-2-ck-materials/step-2-ck-sample-test-questions. It has the same wording, with links to `orientation.nbme.org/Launch/USMLE/STPF2` and `starttest.com/custom/usmle/?stpf2`.
- Step 1 PDF [fetched]: https://www.usmle.org/sites/default/files/2021-10/Step_1_Sample_Items.pdf
  - "This booklet was updated April 2026. For Public Release"
  - > "Copyright © 2026 by the Federation of State Medical Boards of the United States, Inc. (FSMB), and National Board of Medical Examiners (NBME). All rights reserved."
  - Contents: Blocks 1–6, items 1–119.
- Step 2 CK PDF [fetched]: https://www.usmle.org/sites/default/files/2021-10/Step2_CK_Sample_Questions.pdf
  - "This booklet was updated July 2023. For Public Release"
  - > "Copyright © 2023 by the Federation of State Medical Boards ... and the National Board of Medical Examiners® (NBME®). All rights reserved."
  - Contents: Blocks 1–3, items 1–120.
- Site footer on every usmle.org page [fetched]:
  > "Copyright © 1996-2026 Federation of State Medical Boards (FSMB) and National Board of Medical Examiners (NBME). All rights reserved."
- **usmle.org terms of use:** none found. `https://www.usmle.org/terms-use` returned 404, and the footer links only to Privacy, Internet-based ads, Cookies and the Consumer Health Data Privacy Policy. Status: **UNKNOWN / no public reuse licence found.**
- 2027 Bulletin [fetched]: https://www.usmle.org/sites/default/files/2026-08/2027Bulletin.pdf
  - > "Examination materials are the confidential, copyrighted property of the USMLE program. If you reproduce and/or distribute any examination materials, by any means, including by memorizing and reconstructing them, you are violating the legal rights of the USMLE program."
  - > "More than 100 free sample test questions for each Step exam are available via USMLE.org in both PDF format and as an interactive testing experience."
- **Conclusion:** the samples are "For Public Release" but are "All rights reserved", and no reuse licence exists. **Link only**, to the usmle.org page or PDF, or open the official interactive experience in a new tab. Do not store, re-render, or re-type the items.
- **Official embeddable practice:** none found. No page grants iframe or embedding rights. The interactive experiences live on `orientation.nbme.org` and `starttest.com`. NBME's site terms forbid use "on any other website" without consent (see A2). Open them in a new tab only.
- "Free 120": no page on usmle.org or nbme.org uses this name. It is the community nickname for these sample sets. NBME's CBSSA FAQ [fetched] says the Prometric practice session "uses the same sample test materials provided by the USMLE registration entities and at the USMLE website".
- Step 3 vs Step 2 CK: **CCS (Primum computer-based case simulation) is a Step 3 feature, not Step 2 CK.** USMLE FAQ [fetched], https://www.usmle.org/common-questions/practice-materials:
  > "Practice with six CCS cases and receive case-related feedback by accessing the Step 3 Interactive Testing Experience"

### A2. NBME self-assessments and site terms

- NBME Terms of Use [fetched]: https://www.nbme.org/terms-of-use/
  > "Permission to use, copy and distribute material available at NBME's website is granted, provided that (1) the copyright and permission notices contained herein appear in all reproductions, (2) the use of information from this site is for noncommercial, educational, and scientific purposes only, (3) no documents or images from this site are modified in any way, (4) no copyright, trademark or other proprietary notices are removed or modified, and (5) no documents or images from this site are used on any other website or downloaded to any other networked computer environment without the express written consent of the National Board of Medical Examiners ..."
  > "Any rights not expressly granted herein are reserved by the National Board of Medical Examiners."
- Self-assessments are paid products [fetched]: https://www.nbme.org/examinees/self-assessments/comprehensive-basic-science-self-assessment/ lists "Price starts at $62". They are bought through the MyNBME portal.
- **Conclusion:** **link only.** Clause (5) bars putting NBME documents or images on another website without written consent.

### A3. Datasets

| Dataset | Licence (exact) | Provenance of items | Verdict |
|---|---|---|---|
| **MedQA (Jin et al.)** | GitHub LICENSE [fetched] https://raw.githubusercontent.com/jind11/MedQA/master/LICENSE: "MIT License / Copyright (c) 2022 Di Jin" (covers "the Software"). The data is on Google Drive, linked from the README. bigbio card [fetched] https://huggingface.co/datasets/bigbio/med_qa/raw/main/README.md: `license: unknown`, `bigbio_license_shortname: UNKNOWN` | Paper [fetched] https://arxiv.org/pdf/2009.13081: "For USMLE and MCMLE datasets, we scraped several websites that provide the question banks". Table A.11 lists USMLE sources as **https://step1.medbullets.com, https://www.amboss.com/us/usmle, https://www.lecturio.com/usmle-step-1**. Also: "To comply with fair use of law, we shuffle the order of answer options and randomly delete one of the wrong options". The authors relied on fair use, not a licence. | **Do not import.** The US items are scraped commercial qbank content. The MIT licence covers the code, not those third parties' rights. Use for offline evaluation at most. |
| **HEAD-QA** | HF card [fetched] https://huggingface.co/datasets/dvilares/head_qa/raw/main/README.md: "The dataset is licensed under the MIT License". Homepage [fetched] https://aghie.github.io/head-qa/ quotes the Ministry conditions: "The denaturalization of the content of the information is prohibited in any circumstance. The user is obliged to cite the source of the documents subject to reuse. The user is obliged to indicate the date of the last update of the documents object of the reuse." | Spanish MIR and other specialization exams (Ministerio de Sanidad) | Legally reusable with citation and date, and without "denaturalization". Not USMLE-style. Low value. |
| **PubMedQA** | GitHub LICENSE [fetched] https://raw.githubusercontent.com/pubmedqa/pubmedqa/master/LICENSE: "MIT License / Copyright (c) 2019 pubmedqa" | Built from PubMed abstracts. Abstract copyright belongs to publishers and authors (see PMC notice, B2). The MIT licence covers the repo; licence of the abstract text itself: **UNKNOWN / per-article**. | Usable for EBM/biostatistics practice only with care. Prefer linking to PubMed over storing abstracts. |
| **MedExpQA** | HF card [fetched] https://huggingface.co/datasets/HiTZ/MedExpQA/raw/main/README.md: `license: cc-by-4.0`. Based on "the commented MIR exams from the Antidote CasiMedicos dataset" (434/63/125 train/val/test) | Spanish MIR exams plus CasiMedicos explanations | CC BY 4.0, with attribution. Small and not USMLE-aligned. The upstream CasiMedicos rights chain was **not verified**. |
| **MMLU medical subsets** | HF card [fetched] https://huggingface.co/datasets/cais/mmlu/raw/main/README.md links the MIT LICENSE. LICENSE [fetched] https://raw.githubusercontent.com/hendrycks/test/master/LICENSE: "MIT License / Copyright (c) 2020 Dan Hendrycks" | Source of individual items: **UNKNOWN** (not stated on the card) | MIT on its face, but item provenance is unverified. Eval only. |
| MedMCQA (from the starting list) | Not re-fetched this pass. The starting list says Apache-2.0. | Indian AIIMS/NEET-PG | Not verified here |

**Genuinely CC-licensed USMLE-style MCQ banks:** **none found.** A web search [search-only] turned up only commercial banks, free-but-unlicensed banks (e.g. MedAll), and the USMLE samples.
- Wikiversity pages are CC BY-SA 4.0. Footer [fetched] https://en.wikiversity.org/wiki/WikiJournal_of_Medicine: "Text is available under the Creative Commons Attribution-ShareAlike License". No curated USMLE-style quiz set was found there. WikiJournal of Medicine publishes articles, not MCQs.
- **OpenStax review questions are now NC.** See A4 and B10.

### A4. Generating ORIGINAL vignette MCQs with LLMs

- Facts and ideas are not protected. 17 U.S.C. §102(b) [fetched] https://www.law.cornell.edu/uscode/text/17/102:
  > "In no case does copyright protection for an original work of authorship extend to any idea, procedure, process, system, method of operation, concept, principle, or discovery, regardless of the form in which it is described, explained, illustrated, or embodied in such work."

  So writing new vignettes that test the same medical facts is fine. Copying or closely paraphrasing a specific qbank item's expression is not. Supporting controls: no commercial-qbank text in prompts or RAG (already a CLAUDE.md rule), and a near-duplicate check against any reference text.
- AI output copyrightability. US Copyright Office, Part 2 report [fetched] https://www.copyright.gov/ai/Copyright-and-Artificial-Intelligence-Part-2-Copyrightability-Report.pdf:
  > "Copyright does not extend to purely AI-generated material, or material where there is insufficient human control over the expressive elements."
  > "The Office concludes that, given current generally available technology, prompts alone do not provide sufficient human control to make users of an AI system the authors of the output."

  Implication: Naadi may not own copyright in raw LLM-generated items unless humans edit them substantially. This affects protecting the bank, not using it.
- Using source text for generation. Part 3 (training) is a pre-publication report [fetched index] https://www.copyright.gov/ai/. Not analysed here.
- **OpenStax now requires permission for LLM/AI use** [fetched] https://openstax.org/books/anatomy-and-physiology-2e/pages/preface:
  > "Anyone who intends to incorporate this content (including text, images, and other components) into large language models, use it in AI offerings, use it commercially (including in print), and/or has questions about another use case is welcome to complete our reuse request form."

  **Do not put OpenStax into RAG without permission.**
- **NBME Item-Writing Guide** ("Constructing Written Test Questions for the Health Sciences", **October 2024**, 6th ed.). Landing page [fetched] https://www.nbme.org/educators/item-writing-guide: "Complete the form to receive your PDF download today." A direct PDF also downloads [fetched] https://www.nbme.org/sites/default/files/2021-02/NBME_Item%20Writing%20Guide_R_6.pdf.
  > "Permission to copy and distribute this document is granted by NBME provided that: (1) the copyright and permission notices appear on all reproductions; (2) use of the document is for noncommercial, educational, and scientific purposes only; and (3) the document is not modified in any way. Any rights not expressly granted herein are reserved by NBME."
  > "Copyright © 1996, 1998, 2001, 2002, 2016, 2020, 2024 NBME. All rights reserved."

  **Do not store the guide's text in the repo, prompts or RAG.** Item-writing principles are ideas (§102(b)), so the team can write its own rubric in its own words and link to the guide.

### A5. Step 2 CK guideline sources

- **USPSTF** [fetched] https://www.uspreventiveservicestaskforce.org/uspstf/recommendation-topics/copyright-notice. Reproduction is allowed only unchanged, never for a fee or profit without permission, and with a no-endorsement disclaimer on any adaptation.
  > "AHRQ permits members of the public to reproduce, redistribute, publicly display, and incorporate USPSTF work into other materials provided that it is reproduced without any changes to the work of portions thereof, except as permitted as fair use under the U.S. Copyright Act."
  > "Any adaptations of these electronic documents and resources must include a disclaimer to this effect."
  > "This work may not be reproduced, reprinted, or redistributed for a fee, nor may the work be sold for profit or incorporated into a profit-making venture without the express written permission of AHRQ."
  > "When parts of a recommendation statement are used or quoted, the USPSTF Web page should be cited as the source."

  The page says "Current as of: September 2017".
- **CDC (immunization schedules etc.).** https://www.cdc.gov/other/agencymaterials.html returned **HTTP 403** to curl [not fetched]. Search summary [search-only]: most CDC content "is in the public domain, and may be freely used or reproduced without obtaining copyright permission". Attribution is required, substantive content must not change, logos need permission, and third-party or stock images may be restricted. **Verify before relying on this.**
- **NLM (US government works)** [fetched] https://www.nlm.nih.gov/web_policies.html:
  > "Works produced by the U.S. government are not subject to copyright protection in the United States. Any such works found on National Library of Medicine (NLM) Web sites may be freely used or reproduced without permission in the U.S. Please acknowledge NLM as the source of the information by including the phrase "Courtesy of the National Library of Medicine" or "Source: National Library of Medicine.""
- **AHA / ACC guidelines.** heart.org returned **403** and acc.org/About-ACC/Permissions returned **404**. Search summary only [search-only]: AHA requires a written permission request, with fees of $100 per item plus $30 for joint ACC/AHA material; no commercial use. ACC copyright is held via Elsevier. **Treat as link-only. UNKNOWN: any open licence.**
- **CCS-style case content:** CCS is Step 3 only (A1). No openly licensed CCS case source was found (**UNKNOWN**). Write original cases.

---

## B. Media

### B1. Wikimedia Commons (licences are per file)

- [fetched] https://commons.wikimedia.org/wiki/Commons:Licensing:
  > "Wikimedia Commons only accepts free content, that is, images and other media files that are not subject to copyright restrictions which would prevent them being used by anyone, anytime, for any purpose."
  > "Media licensed exclusively under non-commercial only licenses (like CC BY-NC-SA) are not accepted either."
- [fetched] https://commons.wikimedia.org/wiki/Commons:Reusing_content_outside_Wikimedia:
  > "some licenses require that the original creator be attributed; some licenses require that the specific license be identified when reusing ...; and some licenses require that if you modify the work, your modifications must also be similarly freely licensed."
  > "Clicking on an image or media file on Wikimedia Commons will take you to the information page for that file. This will list information supplied by the uploader, including the copyright status, the copyright owner, and the license conditions."
- Verdict: **storable**. Record the per-file licence, author and URL in `sources`, and honour share-alike for derivatives of BY-SA files.
- The Open Pathology project curates CC pathology and histology images there [fetched] https://en.wikipedia.org/wiki/Wikipedia:Open_Pathology_Project:
  > "This project involves developing, curating and sharing pathology related educational material under CC licenses."

### B2. Open-i (NLM) and its PMC source articles

- The Open-i API returns per-image `ccLicense` and `licenseURL` fields [fetched]: `https://openi.nlm.nih.gov/api/search?query=pneumothorax&m=1&n=2&lic=by`.
  - Example results: `PMC11509409 by https://creativecommons.org/licenses/by/4.0/` and `PMC2784978 bync https://creativecommons.org/licenses/by-nc/4.0/`.
  - **Even with `lic=by`, a BY-NC item came back, so filter on the `ccLicense` field yourself.**
  - Some records have no `ccLicense` field at all. Treat those as all rights reserved.
- PMC copyright notice [fetched] https://pmc.ncbi.nlm.nih.gov/about/copyright/:
  > "In the absence of a copyright statement, users should assume that standard copyright protection applies"
  > "Systematic downloading of batches of articles from the main PMC web site, in any way, is prohibited because of copyright restrictions."
  > "Please note open access and public domain articles may still contain photographs or illustrations copyrighted by other commercial organizations or individuals that may not be used without obtaining prior approval from the holder of the copyright."
- Verdict: store only `ccLicense in {by, cc0}` items, or by-sa if share-alike is acceptable, with attribution. Bulk access should use the PMC OA web service or FTP, not scraping.

### B3. PEIR Digital Library (UAB)

- **Not fetched.** curl reported "SSL certificate problem: self-signed certificate in certificate chain", and TLS verification was not disabled.
- [search-only]: "non-profit, non-commercial, non-political, educational uses"; "non-transferable, nonexclusive license"; no use "as a substantial component in the curriculum ... unless specifically authorized by UAB in writing"; UAB retains all copyrights.
- Verdict: **link only** (or get written permission). Re-verify https://peir.path.uab.edu/wiki/Pathology_Education_Instructional_Resource:License.

### B4. Servier Medical Art: **CC BY 4.0, confirmed**

- [fetched] https://smart.servier.com/terms-of-use:
  > "The medical images provided in the Servier Medical Art library are licensed under the Creative Commons Attribution 4.0 International License (CC BY 4.0)."
  > "Share — Copy and redistribute the images in any medium or format, including for commercial purposes. Adapt — Remix, transform, and build upon the images for any purpose, including commercial use."
  > "You must give appropriate credit to Servier Medical Art, provide a link to the license, and indicate if changes were made."
  > Sample: "Images provided by Servier Medical Art (https://smart.servier.com/), licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)."
  > Exclusion: "This Site Content is protected under applicable copyright and intellectual property laws and is not covered under the CC BY 4.0 license."

  Trademarks and logos are excluded.
- Verdict: **storable**, with attribution.

### B5. Radiopaedia

- **Not fetched.** `images.radiopaedia.org/licence`, `radiopaedia.org/licence` and `/terms` all returned **HTTP 406** to curl.
- [search-only]: "modified Creative Commons Attribution-Non-commercial-Share Alike 3.0 Unported License. Further details of these modifications are outlined in our Terms of Use". Attribution must name the case contributor and Radiopaedia (the rID is recommended).
- Indirect confirmation [fetched] https://wikimsk.org/wiki/Help:Creative_Commons: the attribution template uses "|license=CC BY-NC-SA 3.0".
- Verdict: NC-SA plus extra terms, so **link only** if Naadi is commercial. Re-verify the terms page.

### B6. Histology and pathology slide sets

- **University of Michigan Histology** [fetched] https://histology.medicine.umich.edu/:
  > "The material provided on this website and the manual is copyrighted and a permission is required for any commercial use (including textbooks for sale and publications that are not open access). Otherwise this material is made available under a BY-SA-NC Creative Commons license 4.0. Use of any image(s) in this manual for training of AI algorithms, models, systems or applications for commercial use is strictly prohibited."

  Footer: "Except where otherwise noted, this work is licensed under a Creative Commons Attribution-Noncommercial-Share Alike 3.0 License".
  - The page shows both 4.0 and 3.0; record the one shown on each page.
  - UMich Pathology Virtual Slide Box [fetched] https://www.pathology.med.umich.edu/slides/: "this site is now only available to Michigan Medicine staff, faculty, and medical trainees."
  - Verdict: **NC; link only.**
- **WebPath (Mercer, E. C. Klatt)** [fetched] https://webpath.med.utah.edu/:
  > "WebPath® is for your personal and noncommercial use. No one may modify, copy, distribute, transmit, display, or publish any materials contained in WebPath® without prior written permission."

  Verdict: **link only.**
- **Pathpedia:** **not fetched** (proxy CONNECT 502). **UNKNOWN.**
- **AnatomyTOOL survey:** **not fetched** (the site's certificate has expired). **UNKNOWN** beyond the starting list's note.
- Best storable source: Wikimedia Commons CC BY/BY-SA/PD micrographs (B1).

### B7. YouTube Terms and IFrame embedding

- YouTube ToS [fetched] https://www.youtube.com/t/terms:
  > "You may view or listen to Content for your personal, non-commercial use. You may also show YouTube videos through the embeddable YouTube player."
  > It prohibits to "access, reproduce, download, distribute ... or otherwise use any part of the Service or any Content except: (a) as expressly authorized by the Service; or (b) with prior written permission from YouTube and, if applicable, the respective rights holders".
- Embed help [fetched] https://support.google.com/youtube/answer/171780:
  > "The YouTube API Terms of Service and Developer Policies apply to all access and use of the YouTube embedded player."
  - Creators can uncheck "Allow embedding".
  - Privacy Enhanced Mode: "Change the domain for the embed URL in your HTML from https://www.youtube.com to https://www.youtube-nocookie.com."
- Required Minimum Functionality [fetched] https://developers.google.com/youtube/terms/required-minimum-functionality:
  > "Embedded players must have a viewport that is at least 200px by 200px."
  > "an API Client must not initiate an automatic playback until the player is visible and more than half of the player is visible on the page or screen."
  > "You must not display overlays, frames, or other visual elements in front of any ..." (the embedded player)
  > Clients "must provide identification through the HTTP Referer request header"
- Developer Policies [fetched] https://developers.google.com/youtube/terms/developer-policies. You must not:
  - "download, import, backup, cache, or store copies of YouTube audiovisual content without YouTube's prior written approval"
  - "modify, build upon, or block any portion or functionality of a YouTube player"
  - "create, include, or promote features that play content ... from a background player"
- IFrame API errors [fetched] https://developers.google.com/youtube/iframe_api_reference:
  > "101 – The owner of the requested video does not allow it to be played in embedded players. 150 – This error is the same as 101."

  Handle 101/150/153 in the player and fall back to a link.

### B8. Channels: embeddability and their own terms

Method: for each channel, 3 recent video IDs were taken from `youtube.com/@handle/videos`, then `https://www.youtube.com/oembed?url=...` was queried. **All returned HTTP 200.** That suggests embedding is allowed for those videos, but this is not an authoritative check. Embedding is set per video. The authoritative check is the Data API `status.embeddable` field (needs an API key) or runtime error 101/150. Watch pages hit a bot check, so no per-video licence field could be read.

| Channel (ID) | oEmbed sample | Own terms (quote / status) |
|---|---|---|
| Ninja Nerd (UC6QYFutt9cluQ3uSM963_KQ) | 200 ×3 | [fetched] https://www.ninjanerd.org/terms-and-conditions: "You may not modify, copy, distribute, display, send, perform, reproduce, publish, license, create derivative works from ... any information, content ... obtained from or otherwise connected to this Website or Property". It also lists among prohibited uses: "Post a hyper-link to this Website without the prior consent of Ninja Nerd." Those clauses cover ninjanerd.org. YouTube embeds are governed by YouTube ToS. **Do not deep-link ninjanerd.org without consent.** |
| Armando Hasudungan (UCesNt4_Z-Pm41RzpAClfVcg) | 200 ×3 | armandoh.org fetched; no licence or terms text found. **UNKNOWN.** Embed via YouTube only. |
| Khan Academy Medicine (UCJayvjGvKEblkA3KYK1BQQw) | 200 ×3 | khanacademy.org/about/tos served a JS "Client Challenge" and the support article returned 403, so **not fetched**. [search-only] The help article references CC BY-NC-SA, but a library guide says the CC notice was removed, and the ToS reportedly says users "may not download, distribute ... the Licensed Educational Content". **Status conflicting; treat as embed-only, not CC-storable.** |
| Dirty Medicine (UCZaDAUF7UEcRXIFvGZu3O9Q) | 200 ×3 | dirtymedicine.com not reachable (curl exit 56). **UNKNOWN.** Embed only. |
| Osmosis from Elsevier (UCNI0qOojpkhsUtaQ4_2NUhQ) | 200 ×3 | [fetched] https://www.elsevier.com/legal/elsevier-website-terms-and-conditions/elsevier-osmosis-terms-and-conditions: "Elsevier grants you a non-exclusive, non-transferable, right to access and use the content and functionality of the Products solely for your own personal, non-commercial use". Embed only. |
| MedCram (UCG-iSMVtWbbwDDXgXXypARQ) | 200 ×3 | [fetched] https://www.medcram.com/pages/term: the Materials are "licensed to you, not sold. Your purchase gives you a personal, non-transferable license to view the Materials through your own account, for your own education". It also says it does "not cover ... our YouTube channel". Embed only. |
| Lecturio Medical (UCbYmF43dpGHz8gi2ugiXr0Q) | 200 ×3 | [fetched] https://www.lecturio.com/terms-and-conditions/: "You may not scrape, copy, display, distribute, modify, publish, reproduce, store, transmit, post, translate, or create derivative works from, including with artificial intelligence tools, or in any way exploit any part of the Content except that you may make use of the Content for your own personal, non-commercial use". It also says "You may link to the educational content ... for internal use only ... is not used for any commercial use". Embed the public YouTube videos only; never scrape transcripts. |

### B9. NLM general (images on NLM sites)

- [fetched] https://www.nlm.nih.gov/web_policies.html:
  > "Transmission or reproduction of copyrighted items (beyond that allowed by fair use ...) requires the written permission of the copyright holders."
  > "NLM cannot guarantee the copyright status for any item."
- MedPix has its own licence page (https://medpix.nlm.nih.gov/licensing). **Not fetched (UNKNOWN).**

### B10. OpenStax (correction to the starting list)

The starting list said A&P 2e is CC BY 4.0. **The live page now says CC BY-NC-SA 4.0.** [fetched] https://openstax.org/books/anatomy-and-physiology-2e/pages/preface:

> "Anatomy and Physiology 2e is licensed under a Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA) license"
> "OpenStax-copyrighted images, activities, assessments, and similar components of this book are subject to the same licensing – CC-BY-NC-SA. They can be used for noncommercial purposes with attribution. Commercial use requires permission."

The Microbiology, Biology 2e, Psychology 2e, Intro Sociology 3e and Chemistry 2e preface footers [fetched] all read "Textbook content produced by OpenStax is licensed under a Creative Commons Attribution-NonCommercial-ShareAlike License". The LLM/AI clause is quoted in A4. **This changes the HANDOFF's RAG plan if it assumed CC BY.** Older CC BY editions or forks may exist, but none was verified here.

---

## C. Flashcards (AnKing, Zanki, Pepper, Dorian)

- **AnKing Step Deck.** AnkiHub's official FAQ [fetched] https://community.ankihub.net/t/why-dont-i-have-all-the-media-in-the-anking-step-deck/167300:
  > "the media shared includes only those images that we have licensed or that we reasonably believe are in the public domain. In this regard, the media includes Sketchy, Physeo, Pixorize, and other publicly licensed images. Images found in the First Aid and Additional Resources fields of past decks may or may not be covered by third-party copyrights. The deck may include html that references images found in earlier versions of the deck with undetermined rights to use"
  > "These decks are intended to be used with your purchase of any resource used in them."
- AnkiHub forum, user NickFlint (role not verified on the page), 2023 [fetched] https://community.ankihub.net/t/ankihub-removing-copyrighted-images-if-it-aint-broke-dont-fix-it/1640:
  > "Unfortunately many of the images that were originally used for the deck are from textbooks or resources that we don't have the license too."

  The same thread notes Kaplan and Pathoma images being removed from the Zanki-derived cards.
- First Aid and B&B content is delivered through a paid McGraw Hill integration [fetched] https://community.ankihub.net/t/mcgraw-hill-integration-faq/370074. Its requirements include "You have an active "premium" AnkiHub subscription".
- AnkiHub Terms [fetched] https://www.ankihub.net/terms:
  > "This license is for personal use only, and the deck may not be redistributed, re-uploaded, published, or used for any other purposes without explicit permission from the copyright holder."
  > It prohibits "Reproducing, duplicating, copying, selling, reselling, trading, or exploiting for any commercial purposes, any portion of, or access to, the content".
- **Zanki, Pepper, Dorian:** no licence statement found from any author (**UNKNOWN**). These are community decks built on Kaplan, Pathoma, Sketchy, First Aid and B&B material (per the forum quote above and [search-only] results). No takedown records were found.
- **Is any deck CC-licensed and safe to import?** **None found.**
- **Is linking OK?** Linking to public pages (AnkiHub or AnkiWeb listings) is just a hyperlink; no source prohibits it. Do not host, mirror or import the decks or their media.
- **Recommendation:** generate Naadi's own FSRS cards from open or own content.

---

## Pages not fetched (need network or host access; re-verify)

| Page | Failure |
|---|---|
| cdc.gov | 403 |
| radiopaedia.org / images.radiopaedia.org | 406 |
| heart.org | 403 |
| peir.path.uab.edu | self-signed certificate chain |
| anatomytool.org | expired certificate |
| pathpedia.com | proxy 502 |
| dirtymedicine.com | curl exit 56 |
| khanacademy.org | JS challenge |
| support.khanacademy.org | 403 |
| osmosis.org/terms | 403 |
| acc.org/About-ACC/Permissions | 404 |
| usmle.org/terms-use | 404 (no terms page exists) |

Browser user-agent spoofing to get past bot blocks was not used.
