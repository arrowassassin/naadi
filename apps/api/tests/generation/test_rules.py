"""Prompt rules: what is refused (no model call), what stays allowed, and what is stored (the scrubbed prompt)."""

from __future__ import annotations

import pytest

from naadi_api.generation.rules import MAX_PROMPT_CHARS, check_prompt

ALLOWED = [
    "Labelled cross-section of the nephron, flat vector style",
    "An illustration of the circle of Willis with the arteries in different colours",
    "A cartoon of alveolar gas exchange, with oxygen and carbon dioxide arrows",
    "Diagram of the Krebs cycle as a loop",
    "Picture of the Golgi apparatus next to the endoplasmic reticulum",
    "Drawing of Bundle of His and Purkinje fibers in the ventricular septum",
    "Illustration of an Achilles tendon rupture, schematic",
    "Frank-Starling curve explained with a pressure-volume loop sketch",
    "A friendly cartoon doctor explaining blood pressure to a child, flat illustration",
    "Schematic of psoriasis plaques on skin layers, no people",
    "Drawing of a Smith fracture of the distal radius",
    "Picture of Bell's palsy affecting the facial nerve, schematic",
    "A patient with Graves disease, cartoon style",
]

REFUSED = [
    ("Portrait of Albert Einstein in a lab coat", "prompt_real_person"),
    ("photo of Jane Doe holding a stethoscope", "prompt_real_person"),
    ("Dr. Anthony teaching the heart", "prompt_real_person"),
    ("dr. smith drawing a lung", "prompt_real_person"),
    ("looks like Taylor Swift as a nurse", "prompt_real_person"),
    ("a clinical photograph of my patient's rash", "prompt_patient_photo"),
    ("photorealistic photo of a woman with Graves disease", "prompt_patient_photo"),
    ("patient selfie showing facial palsy", "prompt_patient_photo"),
    ("a gory, blood-soaked surgical scene", "prompt_gore"),
    ("graphic wounds with severed limbs", "prompt_gore"),
    ("naked body for anatomy", "prompt_explicit"),
    # review: names that reached the Space and the stored prompt
    ("my patient Priya Sharma with a rash, cartoon", "prompt_real_person"),
    ("an illustration of a woman named Anita Desai with jaundice", "prompt_real_person"),
    ("portrait of John Smith", "prompt_real_person"),
    ("a cartoon of a patient named Rahul Verma, MRN 12345678", "prompt_real_person"),
    ("a cartoon of a patient named Rahul Verma, MRN [id]", "prompt_real_person"),
    ("cartoon of a patient with MRN 4455", "prompt_patient_info"),
]


@pytest.mark.parametrize("prompt", ALLOWED)
def test_educational_prompts_pass(prompt: str) -> None:
    v = check_prompt(prompt)
    assert v.ok, (prompt, v.code)
    assert v.code is None
    assert v.scrubbed


@pytest.mark.parametrize(("prompt", "code"), REFUSED)
def test_refusals_carry_a_stable_code_and_never_echo_the_prompt(prompt: str, code: str) -> None:
    v = check_prompt(prompt)
    assert not v.ok
    assert v.code == code
    assert v.scrubbed == ""


def test_empty_and_too_long() -> None:
    assert check_prompt("   \n\t ").code == "prompt_empty"
    assert check_prompt("a" * (MAX_PROMPT_CHARS + 1)).code == "prompt_too_long"
    assert check_prompt("a" * MAX_PROMPT_CHARS).ok


def test_stored_prompt_is_scrubbed_and_normalised() -> None:
    v = check_prompt("  heart\x00 diagram   for jane.doe@example.org call +1 415 555 0134  ")
    assert v.ok
    assert "jane.doe@example.org" not in v.scrubbed
    assert "415" not in v.scrubbed
    assert "  " not in v.scrubbed and "\x00" not in v.scrubbed
    assert "[email]" in v.scrubbed


def test_unicode_tricks_are_normalised_before_judging() -> None:
    # full-width letters fold to ASCII under NFKC, so they cannot slip past the word rules
    assert check_prompt("ｇｏｒｙ scene").code == "prompt_gore"
