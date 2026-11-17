"""One live image smoke against naadi-gen. Spends ZeroGPU quota: run only from the deploy-gen workflow's manual
`smoke` input. Reads HF_TOKEN and SPACE_ID from the environment; prints only sizes, never the token."""

from __future__ import annotations

import base64
import os
import sys

from gradio_client import Client


def main() -> int:
    client = Client(os.environ["SPACE_ID"], token=os.environ["HF_TOKEN"], verbose=False, analytics_enabled=False)
    out = client.predict("a simple labelled diagram of a heart, flat vector", 512, 512, 1, api_name="/image")
    data = base64.b64decode(out["data_b64"], validate=True)
    ok = data[:4] == b"RIFF" and data[8:12] == b"WEBP" and out["width"] == 512
    print(f"image smoke: {'ok' if ok else 'FAILED'} ({len(data)} bytes)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
