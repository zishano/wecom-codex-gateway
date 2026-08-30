#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.request
import uuid


def request_json(url: str, token: str, data=None):
    payload = None if data is None else json.dumps(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "X-Dev-Token": token},
        method="GET" if data is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a local mock WeCom message")
    parser.add_argument("message")
    parser.add_argument("--user", default="lmk")
    parser.add_argument("--token", default="local-development-token")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--wait", type=int, default=180)
    args = parser.parse_args()

    before = request_json(f"{args.base_url}/dev/outbox", args.token)["messages"]
    result = request_json(
        f"{args.base_url}/dev/simulate",
        args.token,
        {
            "user_id": args.user,
            "content": args.message,
            "message_id": str(uuid.uuid4()),
        },
    )
    print("accepted:", result["accepted"])
    deadline = time.monotonic() + args.wait
    messages = []
    while time.monotonic() < deadline:
        outbox = request_json(f"{args.base_url}/dev/outbox", args.token)["messages"]
        messages = outbox[len(before):]
        session = request_json(
            f"{args.base_url}/dev/session/{args.user}", args.token
        )
        if messages and session["status"] not in {"queued", "analyzing"}:
            break
        time.sleep(0.5)
    for message in messages:
        print(f"\n[{message['user_id']}]\n{message['text']}")
    if not messages:
        raise SystemExit("No reply was received before the timeout")


if __name__ == "__main__":
    main()
