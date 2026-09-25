"""Push vapi/assistant.json to Vapi, with the system prompt inlined from
prompts/system_prompt.md.

Keeping the prompt in the repo rather than the Vapi dashboard is what makes it
reviewable in git. This script is the one-way sync.

Usage:
    VAPI_API_KEY=... VAPI_ASSISTANT_ID=... BACKEND_URL=https://... \
        python scripts/push_assistant.py
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    api_key = os.environ.get("VAPI_API_KEY")
    assistant_id = os.environ.get("VAPI_ASSISTANT_ID")
    backend_url = os.environ.get("BACKEND_URL", "").rstrip("/")

    missing = [
        name for name, value in
        (("VAPI_API_KEY", api_key), ("VAPI_ASSISTANT_ID", assistant_id), ("BACKEND_URL", backend_url))
        if not value
    ]
    if missing:
        print(f"Missing environment variables: {', '.join(missing)}", file=sys.stderr)
        return 1

    config = json.loads((ROOT / "vapi" / "assistant.json").read_text(encoding="utf-8"))
    prompt = (ROOT / "prompts" / "system_prompt.md").read_text(encoding="utf-8")
    config["model"]["messages"][0]["content"] = prompt

    raw = json.dumps(config).replace("<YOUR-BACKEND-URL>", backend_url)

    request = urllib.request.Request(
        f"https://api.vapi.ai/assistant/{assistant_id}",
        data=raw.encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request) as response:
            print(f"Updated assistant {assistant_id} ({response.status})")
    except urllib.error.HTTPError as exc:
        print(f"Vapi rejected the update ({exc.code}): {exc.read().decode()}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
