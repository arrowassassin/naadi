"""Prompt rules for image and video generation (rules only, no model call).

`check_prompt` normalises and scrubs a prompt, then refuses what Naadi will not generate:

- real people (an honorific or a "photo / portrait / likeness of" cue followed by a name),
- patient photographs (a photorealistic human subject, or a patient with a photo, scan or selfie),
- graphic gore and violence,
- explicit sexual content.

Educational anatomy, pathology and mechanism illustrations are fine ("illustration of the circle of Willis", "a
cartoon of alveolar gas exchange"): medical eponyms and anatomical terms are allow-listed so they are not read as
names. The verdict carries a stable `code` (the client words it with ui_copy); the refused text is never echoed.
The prompt that is stored and sent to the Space is `scrubbed`, never the raw input.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from ..llm_privacy import scrub_generic

MAX_PROMPT_CHARS = 500

RefusalCode = str

_WS = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_HONORIFIC = re.compile(
    # abbreviations with a full stop name a person whatever the case ("dr. smith"); full titles need a capitalised name
    r"\b(?i:dr|mr|mrs|ms|prof)\.\s*[A-Za-z][\w'\u2019-]+|"
    r"\b(?i:professor|president|senator|governor|minister|pope|king|queen|prince|princess|sir|lord|lady|sheikh|"
    r"ayatollah|chancellor|emperor|saint)\s+[A-Z][\w'\u2019-]+",
)
# "photo of Jane Doe", "portrait of Einstein", "looks like Taylor Swift", "depicting Elon Musk" ...
_PERSON_CUE = re.compile(
    r"\b(?:photos?|photograph(?:s)?|pictures?|portraits?|likeness|caricature|selfie|face|headshot|cartoon|"
    r"painting|drawing|image|picture)\s+(?:of|depicting)\s+|"
    r"\b(?:depicting|resembling|looks?\s+like|looking\s+like|lookalike\s+of|as\s+played\s+by|"
    r"in\s+the\s+likeness\s+of)\s+",
    re.IGNORECASE,
)
_NAME_WORD = r"[A-Z][a-z'’-]{1,}(?:\s+[A-Z][a-z'’-]{1,})*"
_CAPITALISED_RUN = re.compile(_NAME_WORD)

# Words that, inside a capitalised run, mark an anatomical / medical term instead of a person.
_ANATOMY_WORDS = frozenset(
    w.lower()
    for w in [
        "Circle",
        "Loop",
        "Bundle",
        "Islets",
        "Islet",
        "Apparatus",
        "Cycle",
        "Nerve",
        "Artery",
        "Vein",
        "Muscle",
        "Tendon",
        "Ligament",
        "Gland",
        "Duct",
        "Canal",
        "Valve",
        "Node",
        "Cells",
        "Cell",
        "Body",
        "Bodies",
        "Lobe",
        "Gyrus",
        "Cortex",
        "Nucleus",
        "Tract",
        "Plexus",
        "Ganglion",
        "Ring",
        "Triangle",
        "Space",
        "Layer",
        "Fibers",
        "Fibres",
        "Reflex",
        "Syndrome",
        "Disease",
        "Law",
        "Curve",
        "Effect",
        "Phenomenon",
        "Sign",
        "Point",
        "Line",
        "Pouch",
        "Fold",
        "Fossa",
        "Foramen",
        "Membrane",
        "Process",
        "Notch",
        "Cartilage",
        "Vessel",
        "Sinus",
        "Trunk",
        "Chamber",
        "Pathway",
        "Ventricle",
        "Atrium",
        "Cavity",
        "System",
        "Organ",
        "Gland",
        "Hormone",
        "Receptor",
        "Organelle",
        "Kidney",
        "Liver",
        "Lung",
        "Heart",
        "Brain",
        "Skull",
        "Spine",
        "Bone",
        "Joint",
    ]
)
_EPONYMS = frozenset(
    w.lower()
    for w in [
        "Achilles",
        "Willis",
        "Henle",
        "Krebs",
        "Golgi",
        "Langerhans",
        "His",
        "Broca",
        "Wernicke",
        "Purkinje",
        "Schwann",
        "Kupffer",
        "Sertoli",
        "Leydig",
        "Meissner",
        "Pacini",
        "Ruffini",
        "Merkel",
        "Bowman",
        "Malpighi",
        "Glisson",
        "Hering",
        "Corti",
        "Eustachian",
        "Fallopian",
        "Graafian",
        "Bartholin",
        "Cowper",
        "Skene",
        "Wharton",
        "Stensen",
        "Meckel",
        "Hartmann",
        "Douglas",
        "Morison",
        "Calot",
        "Hesselbach",
        "Scarpa",
        "Camper",
        "Colles",
        "Cooper",
        "Poupart",
        "Gimbernat",
        "Vater",
        "Oddi",
        "Mirizzi",
        "Barrett",
        "Zenker",
        "Meniere",
        "Bell",
        "Parkinson",
        "Alzheimer",
        "Huntington",
        "Crohn",
        "Hashimoto",
        "Graves",
        "Addison",
        "Cushing",
        "Down",
        "Turner",
        "Klinefelter",
        "Marfan",
        "Ehlers",
        "Danlos",
        "Wilson",
        "Gilbert",
        "Hodgkin",
        "Burkitt",
        "Kaposi",
        "Raynaud",
        "Paget",
        "Pott",
        "Colles",
        "Starling",
        "Frank",
        "Bohr",
        "Haldane",
        "Henderson",
        "Hasselbalch",
        "Michaelis",
        "Menten",
        "Lineweaver",
        "Burk",
        "Hardy",
        "Weinberg",
        "Mendel",
        "Gram",
        "Ziehl",
        "Neelsen",
        "Giemsa",
        "Wright",
        "Romberg",
        "Babinski",
        "Chvostek",
        "Trousseau",
        "Murphy",
        "Kernig",
        "Brudzinski",
        "Cheyne",
        "Stokes",
        "Kussmaul",
        "Biot",
        "Virchow",
        "Charcot",
        "Osler",
        "Janeway",
        "Roth",
        "Beck",
        "Cullen",
        "Grey",
        "Turner",
        "Courvoisier",
        "Troisier",
        "Virchow",
        "Mallory",
        "Weiss",
        "Boerhaave",
        "Osgood",
        "Schlatter",
        "Legg",
        "Calve",
        "Perthes",
        "Salter",
        "Harris",
        "Bankart",
        "Hill",
        "Sachs",
        "Lisfranc",
        "Chopart",
        "Pott",
        "Smith",
        "Bennett",
        "Monteggia",
        "Galeazzi",
        "Jones",
        "Segond",
        "Maisonneuve",
        "Tillaux",
        "Wolff",
        "Parkinson",
        "White",
        "Brugada",
        "Dressler",
        "Prinzmetal",
        "Kawasaki",
        "Takayasu",
        "Wegener",
        "Goodpasture",
        "Churg",
        "Strauss",
        "Behcet",
        "Sjogren",
        "Guillain",
        "Barre",
        "Eaton",
        "Lambert",
        "Duchenne",
        "Becker",
        "Gower",
        "Guyon",
        "Erb",
        "Klumpke",
        "Horner",
        "Pancoast",
        "Brown",
        "Sequard",
        "Weber",
        "Rinne",
        "Dix",
        "Hallpike",
        "Epley",
        "Snellen",
        "Ishihara",
        "Amsler",
        "Schirmer",
        "Weigert",
        "Nissl",
        "Betz",
        "Meynert",
        "Heschl",
        "Wada",
        "Papez",
        "Reil",
        "Rolando",
        "Sylvius",
        "Monro",
        "Magendie",
        "Luschka",
        "Galen",
        "Vieussens",
        "Thebesian",
        "Tarlov",
        "Rexed",
        "Clarke",
        "Lissauer",
        "Edinger",
        "Westphal",
        "Darkschewitsch",
        "Cajal",
        "Dale",
        "Hering",
        "Breuer",
        "Bainbridge",
        "Bezold",
        "Jarisch",
        "Cushing",
        "Valsalva",
        "Mueller",
        "Mullerian",
        "Wolffian",
        "Skene",
        "Gartner",
        "Rathke",
        "Seessel",
        "Hesselbach",
        "Hunter",
        "Hunters",
        "Guyton",
    ]
)
_STRUCTURE_WORDS = frozenset({"of", "the", "and", "in", "to", "for", "with", "a", "an"})

_PATIENT_PHOTO = re.compile(
    r"\b(?:patient|patients|real\s+patient|someone|my\s+\w+|a\s+person)\b.{0,40}\b(?:photo|photograph|picture|selfie|"
    r"x-?ray|radiograph|scan|mri|ct|ultrasound|image)\b|"
    r"\b(?:photo|photograph|picture|selfie|x-?ray|radiograph|scan|mri|ultrasound)\b.{0,40}\b(?:patient|patients)\b|"
    r"\bclinical\s+(?:photo|photograph|picture|image)s?\b|\bpatient'?s?\s+(?:face|skin|body|chart|record)\b|"
    r"\bmedical\s+records?\b",
    re.IGNORECASE,
)
_PHOTOREAL = re.compile(
    r"\b(?:photoreal(?:istic)?|photo-?realistic|hyper-?realistic|hyperreal|real\s+photo|realistic\s+photo(?:graph)?|"
    r"documentary\s+photo|dslr|35\s*mm\s+photo|stock\s+photo|photograph)\b",
    re.IGNORECASE,
)
_HUMAN_SUBJECT = re.compile(
    r"\b(?:man|woman|men|women|boy|girl|child|children|kid|baby|infant|toddler|teen(?:ager)?|adult|person|people|"
    r"patient|patients|human\s+face|face|faces|portrait|selfie|nurse|doctor|physician|surgeon|elderly|mother|father)\b",
    re.IGNORECASE,
)
_GORE = re.compile(
    r"\b(?:gore|gory|bloody|blood[\s-]?(?:soaked|splatter(?:ed)?|bath)|splatter|mutilat\w*|dismember\w*|"
    r"decapitat\w*|beheaded|beheading|disembowel\w*|severed|torture[sd]?|torturing|slaughter\w*|massacre|"
    r"corpses?|cadavers?\s+in\s+pieces|graphic\s+(?:injur\w+|wounds?|violence|death)|open\s+wounds?\s+close-?up|"
    r"shot\s+in\s+the\s+head|suicide|self[\s-]?harm|execution|brutal(?:ly)?\s+(?:murder|kill)\w*)\b",
    re.IGNORECASE,
)
_EXPLICIT = re.compile(
    r"\b(?:nude|naked|nsfw|porn\w*|erotic|sexual(?:ly)?\s+explicit|explicit\s+sex\w*|hentai|genitals?\s+exposed|"
    r"topless|lingerie)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class PromptVerdict:
    ok: bool
    code: RefusalCode | None
    scrubbed: str


def normalise(prompt: str) -> str:
    """NFKC, control characters out, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", prompt)
    text = _CONTROL.sub(" ", text)
    return _WS.sub(" ", text).strip()


def _is_anatomical(run: str) -> bool:
    words = [w.lower().strip("'’") for w in run.split()]
    return any(w in _ANATOMY_WORDS or w in _EPONYMS or w.rstrip("s") in _EPONYMS for w in words)


def _names_a_person(text: str) -> bool:
    if _HONORIFIC.search(text):
        return True
    for cue in _PERSON_CUE.finditer(text):
        rest = text[cue.end() :]
        name = _CAPITALISED_RUN.match(rest)
        if name is None:
            continue
        run = name.group(0)
        if not _is_anatomical(run):
            return True
    return False


def check_prompt(prompt: str, *, max_chars: int = MAX_PROMPT_CHARS) -> PromptVerdict:
    """Normalise, scrub and judge a prompt. `ok` False carries a stable refusal `code`."""
    text = normalise(prompt)
    if not text:
        return PromptVerdict(False, "prompt_empty", "")
    if len(text) > max_chars:
        return PromptVerdict(False, "prompt_too_long", "")
    scrubbed = scrub_generic(text)
    # Judge the scrubbed text: an email or phone number in a prompt is a privacy problem, not a request to refuse.
    if _names_a_person(scrubbed):
        return PromptVerdict(False, "prompt_real_person", "")
    if _PATIENT_PHOTO.search(scrubbed) or (_PHOTOREAL.search(scrubbed) and _HUMAN_SUBJECT.search(scrubbed)):
        return PromptVerdict(False, "prompt_patient_photo", "")
    if _GORE.search(scrubbed):
        return PromptVerdict(False, "prompt_gore", "")
    if _EXPLICIT.search(scrubbed):
        return PromptVerdict(False, "prompt_explicit", "")
    return PromptVerdict(True, None, scrubbed)


__all__ = ["MAX_PROMPT_CHARS", "PromptVerdict", "check_prompt", "normalise"]
