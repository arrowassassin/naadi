# Legally Reusable Content Sources for Naadi (USMLE Step 1 / 2 CK / 3 study site), as of Nov 2026

Research date: 2026-11-15. I made about 18 tool calls. Pages were fetched live wherever possible. Anything I could not check against a primary source is listed under Gaps and must not be treated as verified.

## Q1. YouTube channels for USMLE (URLs, scale, coverage, embedding)

### Takeaway
Only three channel identities could be checked against a third-party stats page this session, and only one had a dated subscriber count. Developers should not hard-code the subscriber, cadence or playlist claims below. Build the allowlist from channel IDs and verify each one by calling `channels.list` (1 quota unit) and `playlists.list`. Embeddability is a per-video setting, so it has to be checked per video with `videos.list` `status.embeddable`, not per channel.

### Cited Findings
- Ninja Nerd: handle **@ninjanerdofficial**, channel ID `UC6QYFutt9cluQ3uSM963_KQ`. The vidIQ page did not show a subscriber count. — [vidIQ](https://vidiq.com/youtube-stats/channel/UC6QYFutt9cluQ3uSM963_KQ)
- MedCram ("MedCram - Medical Lectures Explained CLEARLY"): handle **@medcram**, channel ID `UCG-iSMVtWbbwDDXgXXypARQ`. No subscriber count was shown. — [vidIQ](https://vidiq.com/youtube-stats/channel/UCG-iSMVtWbbwDDXgXXypARQ)
- Dr. Najeeb Lectures: channel ID `UCPHpx55tgrbm8FrYYCflAHw`, about 2.3M subscribers and about 1.57K videos, uploading about 0.5 videos per week ("Low"). This data is dated May 14, 2025. The channel links to the paid site DrNajeebLectures.com, so only part of the catalog is free on YouTube. — [vidIQ](https://vidiq.com/youtube-stats/channel/UCPHpx55tgrbm8FrYYCflAHw)
- The same search turned up other medical channels the team could consider: Medicosis Perfectionalis (`UCl-J-ovSJhA3or73Q2uVpow`) and Drbeen Medical Lectures (`UCrtd2wePvAl6RN_D-9jWVQQ`). — [vidIQ Medicosis](https://vidiq.com/youtube-stats/channel/UCl-J-ovSJhA3or73Q2uVpow), [vidIQ Drbeen](https://vidiq.com/youtube-stats/channel/UCrtd2wePvAl6RN_D-9jWVQQ)

### Inferences
- I did not verify the other requested channels this session: Dirty Medicine, Armando Hasudungan, Osmosis, Randy Neil MD, Mehlman Medical, Divine Intervention, Strong Medicine, Khan Academy Medicine, Rhesus Medicine, Physeo, Zero to Finals, The Noted Anatomist, AMBOSS, Lecturio and CrashCourse. The planned allowlist workflow is:
  1. Resolve each handle to a channel ID with `channels.list?forHandle=@handle` (1 unit).
  2. Read `statistics.subscriberCount` and `contentDetails.relatedPlaylists.uploads`.
  3. Use `playlists.list?channelId=` to see how the channel organizes its playlists by system.
- Commercial-brand channels (Osmosis/Elsevier, AMBOSS, Lecturio, Dr Najeeb) put free teaser videos on YouTube and keep their full libraries behind paywalls. Embedding the free YouTube videos is fine under YouTube's terms. Copying their slides or notes is not.
- Quality and editorial fit, such as how well a channel matches First Aid or Step 2 CK content, still needs a human review pass for each channel. This session did not research it.

### Gaps
- Verified URLs, subscriber counts, update cadence and playlist structure for every channel except the three above. The stats aggregators either returned no numbers or returned stale ones (the Najeeb figure is from May 2025).
- Whether "Speeding Lecture" and "Physeo" have meaningful free YouTube catalogs. Unverified.

## Q2. YouTube terms for third-party sites (embedding, caching, quota, mapping topics to videos)

### Takeaway
Embedding through the official player is allowed, under strict conditions:
- No downloading or re-hosting of video.
- No blocking or changing ads or the player.
- Non-authorized metadata may be stored for at most 30 days.
- The site must make clear that YouTube is the source.
- The site needs a privacy policy that discloses its use of YouTube API Services.

**Critical for a paid site:** the policies say a client must not charge users to watch content in an embedded player. Naadi can charge for its own features, but the YouTube embeds should not sit behind a paywall. Counsel should review this.

**Quota has changed:** `search.list` now costs 1 unit but is capped at **100 calls per day** by default. It is no longer 100 units out of 10,000. Bulk mapping of topics to videos should therefore use `playlistItems.list`, not `search.list`.

### Cited Findings
- The policies apply to "API Clients", meaning a website or app that uses YouTube API Services. — [YouTube Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
- Clients must not "download, import, backup, cache, or store copies of YouTube audiovisual content without YouTube's prior written approval" (III.E.1.a). Offline playback is prohibited (III.E.1.b). — [Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
- Clients must not "modify, interfere with, replace, or block advertisements placed or served by YouTube" (III.I.5).
- They must not "modify, build upon, or block any portion or functionality of a YouTube player" (III.I.6).
- They must not "separate, isolate, or modify the audio or video components" (III.I.7). Background playback is prohibited (III.I.9).
- They must not "remove, obscure, alter, or disable any links" in the player (III.I.4).
- They must not nest the player in a hidden iframe lineage (III.I.21).
- Selling ads or sponsorships on or within the player needs prior written approval (III.G.1.c). — [Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
- Clients must not charge users to watch content in an embedded player (III.F.3.a). — [Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
- Clients must not require any action other than pressing play, such as subscribing or liking (III.F.3.b). They must not reward users for viewing, liking or subscribing (III.F.3.c). This rules out gating progress points on video views via YouTube.
- Non-Authorized Data may be stored "but not longer than 30 calendar days" (III.E.4.d). Statistics may not be kept for more than 30 days (III.E.4.b).
- Stored data must be kept reasonably current (III.E.4.e), and the client must not substitute its own figures for API data (III.E.4.h).
- If a user asks for their data to be deleted, the client must delete it within 7 calendar days (III.E.4.g). — [Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
- Attribution:
  - Pages must make clear that YouTube is the source, using YouTube Brand Features per the Branding Guidelines (III.F.2.a).
  - Clients must not obscure YouTube's attribution (III.F.2.c). — [Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
- Privacy and terms:
  - Users must agree to a privacy policy that is prominently displayed, states that the client uses YouTube API Services and links to Google's Privacy Policy (III.A.2).
  - The client must display a link to YouTube's Terms of Service (III.A.1).
  - Clients must check each video's Made for Kids status (III.E.4.j).
  - With autoplay on, YouTube collects data on page load; setting autoplay to false limits that (III.E.4.i). — [Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
- Embedded players must meet the Required Minimum Functionality (III.C.1). The policies page refers to it but does not reproduce it. — [Developer Policies](https://developers.google.com/youtube/terms/developer-policies)
- Quota:
  - The default is 10,000 units per day, resetting at midnight Pacific time.
  - `search.list` and `videos.insert` each have their own default limit of 100 calls per day. `search.list` costs 1 unit.
  - `playlistItems.list`, `videos.list`, `channels.list` and `playlists.list` cost 1 unit each. — [YouTube Data API quota calculator](https://developers.google.com/youtube/v3/determine_quota_cost)

### Inferences
- Recommended pipeline for mapping topics to videos:
  1. Allowlist channel IDs.
  2. Once a week, pull each channel's uploads playlist with `playlistItems.list` (50 items per page, 1 unit per page) plus `videos.list` for title, description, duration and `status.embeddable` (up to 50 IDs per call).
  3. Match videos to the Naadi topic taxonomy offline, using embeddings or keywords over titles, descriptions and chapters, followed by human curation.
  4. Store only video IDs indefinitely. Refresh cached titles and thumbnails within 30 days.
  5. Use `search.list?channelId=...&q=...` only for ad-hoc gap filling, because of the 100-calls-per-day cap.
- Use `youtube-nocookie.com` and lazy-load or use a click-to-load facade (autoplay off) to reduce tracking and meet privacy obligations. Do not overlay anything on the player.
- The no-paywall rule (III.F.3.a) needs legal review. The safest model keeps YouTube embeds on free or ungated pages, or links out to YouTube.

### Gaps
- I did not retrieve the exact text of the Required Minimum Functionality (minimum player size, for example 200x200), the YouTube API Services Terms of Service, or the Branding Guidelines. See https://developers.google.com/youtube/terms/required-minimum-functionality and https://developers.google.com/youtube/terms/api-services-terms-of-service.
- Whether "Authorized Data" (data about users who authenticated with OAuth) has different retention rules. Not checked.

## Q3. Open-licensed text and images: exact licenses

### Takeaway
There are two major changes to plan around:
- **StatPearls is CC BY-NC-ND 4.0.** It cannot be used commercially and cannot be modified, so for a commercial Naadi it is link-only.
- **OpenStax has switched to CC BY-NC-SA.** Anatomy & Physiology 2e now carries CC BY-NC-SA 4.0 and explicitly forbids LLM training or ingestion into generative AI without written permission. The long-standing assumption that "OpenStax = CC BY 4.0" is no longer safe.

Radiopaedia is CC BY-NC-SA 3.0, so it is effectively link-only for a commercial site. US federal works, such as the public-domain parts of MedlinePlus, are the safest material to copy.

### Cited Findings
- **StatPearls:** "Copyright © 2026, StatPearls Publishing LLC". Distributed under "Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International (CC BY-NC-ND 4.0)". It may not be used commercially or altered; it may be redistributed with credit. — [NCBI Bookshelf, StatPearls](https://www.ncbi.nlm.nih.gov/books/NBK430685/)
- **OpenStax (library-wide):** OpenStax "transitioned our textbook library from a mix of CC BY and CC BY-NC-SA licensing to CC BY-NC-SA licensing". "This change will apply to all newly released and updated versions of our titles, with limited exceptions." The page gives no version number, no effective date and no list of the exceptions. — [OpenStax licensing blog](https://openstax.org/blog/openstax-licensing), [openstax.org/license](https://openstax.org/license)
- **OpenStax Anatomy & Physiology 2e** is now "Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA)". Its terms:
  - Use is noncommercial only, and derivatives must share alike.
  - Art without credit in the text is attributed as "Copyright Rice University, OpenStax, under CC BY-NC-SA 4.0 license".
  - Digital copies must show the attribution on every page that includes OpenStax content.
  - OpenStax and Rice names, logos and trademarks are excluded from the license.
  - The book "may not be used to train large language models or ingested into generative AI systems without OpenStax's prior written permission". — [OpenStax A&P 2e preface](https://openstax.org/books/anatomy-and-physiology-2e/pages/preface)
- **Radiopaedia:** "Creative Commons Attribution-Non-commercial-Share Alike 3.0 Unported License", "except where stated otherwise in the image or case description". You may not use the work commercially, "either directly or indirectly". Adaptations must be shared alike.
  - Image credit names the contributor (for example, "Dr Frank Gaillard"). Online use should also link to the original case, the home page and the license page.
  - Text use cites the article and links back to it.
  - Permission beyond the license can be requested through Radiopaedia, which forwards requests to the contributing user. — [Radiopaedia licence](https://radiopaedia.org/licence)
- **MedlinePlus:**
  - Public domain: federal works (health topic summaries, medical test information, Genetics summaries, NLM-watermarked illustrations, MedlinePlus videos and tutorials). MedlinePlus asks to be credited as the source.
  - Copyrighted: the A.D.A.M. Medical Encyclopedia (articles and videos), ASHP drug monographs, and any image not marked as public domain. These need written permission beyond fair use.
  - Copyrighted pages are labeled near the bottom. Single direct links are allowed. Framing MedlinePlus pages under another domain is prohibited, and the RSS feeds are for personal use only. — [MedlinePlus copyright](https://medlineplus.gov/copyright.html)
- **CDC PHIL:** CDC welcomes use for "reference, teaching, presentation, and public health messages". The About page does **not** state the public-domain or copyright status; that is likely in the FAQ. Note the redirect: phil.cdc.gov → wwwn.cdc.gov/phil. — [CDC PHIL About](https://wwwn.cdc.gov/phil/about.aspx)

### Inferences
- **For a commercial (paid) Naadi:**
  - StatPearls, Radiopaedia and post-change OpenStax titles all have NC restrictions, so they are **link-only**.
  - StatPearls is also ND, so even a noncommercial Naadi could not excerpt-and-edit it.
  - A "free core tier" structure would not reliably fix the NC problem if the site as a whole is commercial. Counsel should review this.
- Older OpenStax editions released under CC BY 4.0 stay CC BY for the copies that were released that way, because CC licenses are irrevocable. Developers would have to pin those exact archived versions and keep evidence of the license at the time, such as an archived copyright page. I did not verify which editions or archives exist.
- OpenStax's no-AI clause matters for any RAG or LLM pipeline. Do not ingest OpenStax books into AI generation pipelines without written permission.
- In PHIL, each image record has its own copyright field; some images are copyrighted. Check per image.

### Gaps (not verified this session; do not assume)
- Wikimedia Commons: the license is per file (CC BY, CC BY-SA, PD and others). Read each file's license from the Commons API `imageinfo` `extmetadata` (LicenseShortName, Artist, AttributionRequired). Not fetched.
- OpenStax Microbiology and Biology 2e: their current license after the switch. Check each book's preface.
- LibreTexts Medicine: licenses vary per page. Not fetched.
- WebPath (University of Utah), PEIR Digital Library (UAB), Open-i (NLM; image licenses follow each source article), PMC Open Access subset (per-article license: CC0, CC BY, CC BY-NC and others), Wikipedia (CC BY-SA 4.0) and WikiJournal of Medicine. None fetched.
- USPSTF, AHRQ and CDC guidelines are generally US government works, but none was fetched. Some include copyrighted third-party figures.
- Merck Manual Professional terms of use. Not fetched. Assume link-only.

## Q4. Free question sources and reuse terms; commercial Qbank and First Aid risk; AI-generated questions

### Takeaway
I could not retrieve the USMLE terms of use (usmle.org/terms-use failed to load). Until counsel reviews them, treat the official sample questions and content outlines as copyrighted by FSMB and NBME: link to them, do not re-host them. Copying any commercial Qbank (UWorld, AMBOSS) or First Aid text, questions or images would be copyright infringement. Original AI-drafted questions written in NBME item-writing style, with physician review, are the defensible route.

### Cited Findings
- USMLE has a "Permission to Use" page (URL: usmle.org/privacy-policy) and a 2027 Bulletin PDF. Their content was not fetched. — [USMLE Permission to Use](https://www.usmle.org/privacy-policy), [2027 Bulletin](https://www.usmle.org/sites/default/files/2026-08/2027Bulletin.pdf)
- Copies of the official sample questions are widely re-hosted on Scribd. That re-hosting is not evidence that it is authorized. — [Scribd example](https://www.scribd.com/document/708292246/USMLE-Free120-2020-2022)

### Inferences
- Use the official free sample questions only as a link-out ("Take the official USMLE sample test"). Use the content outlines as a taxonomy reference: mapping topics to them is low risk, copying the outline documents is not.
- For AI-generated questions:
  - Generate from Naadi's own notes and from public-domain or CC BY sources only (not OpenStax with its no-AI clause, and not NC/ND sources).
  - Follow the NBME item-writing guide's principles (single best answer, vignette in the stem, no "all of the above").
  - Require physician review and record the reviewer and date.
  - Run similarity checks against known commercial items.
  - Keep provenance logs.

### Gaps
- Exact USMLE terms of use and Permission to Use text, and the NBME "Item-Writing Guide" (formerly "Constructing Written Test Questions") terms. Not retrieved.

## Q5. Podcasts (Divine Intervention, Goljan audio)

### Takeaway
Not researched in depth this session.

### Cited Findings
- None verified.

### Inferences
- Divine Intervention episodes are distributed on public podcast platforms. Embedding through official players (Spotify or Apple embed widgets, or the host's own player) is the only safe option.
- The widely circulated "Goljan audio" recordings are lectures that may be copyrighted by their creator or publisher. Do not host or link to unauthorized copies.

### Gaps
- The official URLs for Divine Intervention, whether embedding is allowed, and who holds the copyright to the Goljan audio. All unverified.

## Q6. Tiered source strategy and attribution component spec

### Takeaway
Use three tiers: (1) embed or link only, (2) copy with attribution under a commercial-compatible license, (3) public domain. Gate every asset with a license field in the content database.

### Cited Findings
- Tier 1, embed or link only, assuming a commercial site:
  - YouTube, through the official player under the Developer Policies — [policies](https://developers.google.com/youtube/terms/developer-policies)
  - StatPearls (CC BY-NC-ND 4.0) — [NCBI](https://www.ncbi.nlm.nih.gov/books/NBK430685/)
  - Radiopaedia (CC BY-NC-SA 3.0) — [licence](https://radiopaedia.org/licence)
  - OpenStax titles now under CC BY-NC-SA — [A&P 2e](https://openstax.org/books/anatomy-and-physiology-2e/pages/preface)
  - MedlinePlus A.D.A.M. and ASHP content — [MedlinePlus](https://medlineplus.gov/copyright.html)
  - Merck Manual and USMLE materials: unverified, so assume link-only.
- Tier 2, copy with attribution: CC BY and CC BY-SA assets such as Wikimedia Commons files and PMC OA articles under CC BY. Licenses vary per item, and **none were verified this session**. Archived OpenStax editions under CC BY 4.0 may also qualify (inference; unverified).
- Tier 3, public domain: MedlinePlus federal content (credit MedlinePlus as the source) — [MedlinePlus](https://medlineplus.gov/copyright.html). PHIL images marked public domain, checked per image. US federal guidelines once verified.

### Inferences
- **Database schema (per asset):** source_id, source_url, title, author or creator, license_code (SPDX-style, for example CC-BY-4.0, CC-BY-SA-4.0, PD-USGov, CC-BY-NC-SA-3.0), license_url, tier (1, 2 or 3), allow_commercial, allow_modify, share_alike, ai_ingest_allowed (false for OpenStax and for NC/ND sources unless permission is obtained), modifications_made, retrieved_at, license_snapshot_url (archived copy), and reviewer.
- **Attribution component (TASL: Title, Author, Source, License):**
  - Render it beneath every Tier 2 or 3 image or excerpt: "'{title}' by {author}, {source link}, licensed under {license link}. {Modified: cropped/annotated}".
  - For Radiopaedia links, include the contributor name plus links to the case, the home page and the license.
  - For YouTube, keep YouTube's own attribution visible and show a source badge.
  - Make the attribution visible without hover, give it alt text, and ship it with exported or printed views.
- **Pipeline gates:**
  - Block publishing when the license code is missing.
  - Block NC or ND assets from paid pages.
  - Block share-alike assets from being combined into proprietary composites without a share-alike license on the composite.
  - Block assets with ai_ingest_allowed = false from LLM/RAG.

### Gaps
- Final legal review of the NC/commercial classification and of the YouTube no-paywall rule.
