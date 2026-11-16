#!/usr/bin/env python3
"""Verify every model in config/models.yaml exists on OpenRouter and report price/capabilities.

Usage: OPENROUTER_API_KEY=... python tools/check_models.py [--config config/models.yaml]
Exit code 1 if any primary model is missing or lacks a capability its role needs.
"""
import argparse, json, os, sys, urllib.request

try:
    import yaml  # pyyaml
except ImportError:
    sys.exit("pip install pyyaml")

NEEDS = {  # role -> required capability checks
    "tutor": {"tools"},
    "structured": {"structured_outputs"},
    "vision": {"image"},
    "author": set(), "reviewer": set(), "router": set(), "long_context": set(),
}

def fetch(url):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {os.environ.get('OPENROUTER_API_KEY','')}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/models.yaml")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config))
    catalog = {m["id"]: m for m in fetch("https://openrouter.ai/api/v1/models")["data"]}
    bad = 0
    for role, spec in cfg["roles"].items():
        if role in ("embeddings", "speech_to_text"):
            print(f"{role:14s} {spec['model']}  (check via /api/v1/embeddings or /audio endpoints)")
            continue
        for i, mid in enumerate([spec["model"]] + spec.get("fallbacks", [])):
            m = catalog.get(mid)
            tag = "primary " if i == 0 else "fallback"
            if not m:
                print(f"{role:14s} {tag} {mid:40s} MISSING"); bad += i == 0; continue
            p = m.get("pricing", {})
            params = set(m.get("supported_parameters", []))
            mods = set(m.get("architecture", {}).get("input_modalities", []))
            have = params | mods
            missing = NEEDS.get(role, set()) - have
            price = f"${float(p.get('prompt',0))*1e6:.2f}/${float(p.get('completion',0))*1e6:.2f} per 1M"
            print(f"{role:14s} {tag} {mid:40s} ctx={m.get('context_length')} {price} {'MISSING CAP '+str(missing) if missing else 'ok'}")
            if missing and i == 0:
                bad += 1
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
