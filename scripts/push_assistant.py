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

    assistant_path = ROOT / "vapi" / "assistant.json"
    try:
        config = json.loads(assistant_path.read_text(encoding="utf-8"))
        prompt = (ROOT / "prompts" / "system_prompt.md").read_text(encoding="utf-8")
        config["model"]["messages"][0]["content"] = prompt
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        print(f"Could not read a valid assistant config from {assistant_path}: {exc}", file=sys.stderr)
        return 1

    # The webhook shared secret is injected here rather than committed, so the
    # repository stays free of credentials while one command still configures
    # everything. It must match VAPI_SECRET on the deployed backend, which
    # rejects every request when the two disagree.
    webhook_secret = os.environ.get("VAPI_SECRET")
    if webhook_secret:
        config.setdefault("server", {})["secret"] = webhook_secret
        # Every tool carries its own server block, and a tool-level server
        # config REPLACES the assistant-level one rather than merging with it.
        # Without the secret on each tool, Vapi posts tool calls with no
        # x-vapi-secret header and the backend rejects them with 401 mid-call.
        for tool in config.get("model", {}).get("tools", []):
            if "server" in tool:
                tool["server"]["secret"] = webhook_secret
    else:
        print(
            "VAPI_SECRET not set — the assistant's server secret will be left as it is. "
            "Set it to match the backend, or the webhook will reject every tool call.",
            file=sys.stderr,
        )

    raw = json.dumps(config).replace("<YOUR-BACKEND-URL>", backend_url)

    request = urllib.request.Request(
        f"https://api.vapi.ai/assistant/{assistant_id}",
        data=raw.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Vapi sits behind Cloudflare, which rejects urllib's default
            # "Python-urllib/3.x" signature with a 403 (error 1010) before the
            # request ever reaches the API. An ordinary User-Agent avoids it.
            "User-Agent": "voice-patient-intake/1.0",
            "Accept": "application/json",
        },
        method="PATCH",
    )
    try:
        with urllib.request.urlopen(request) as response:
            print(f"Updated assistant {assistant_id} ({response.status})")
    except urllib.error.HTTPError as exc:
        print(f"Vapi rejected the update ({exc.code}): {exc.read().decode()}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Could not reach Vapi: {exc.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
