import argparse
import os
import sys
import time

import httpx


def build_start_payload(webhook_url: str, count: int, duration_seconds: int) -> dict:
    return {
        "webhook_url": webhook_url,
        "count": count,
        "duration_seconds": duration_seconds,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Start and inspect a PseudoGram simulator run.")
    parser.add_argument("--webhook-url", required=True, help="Deployed webhook URL ending in /webhook")
    parser.add_argument("--api-key", default=os.getenv("PSEUDOGRAM_API_KEY"))
    parser.add_argument("--base-url", default=os.getenv("PSEUDOGRAM_BASE_URL", "https://pseudogram-api.onrender.com"))
    parser.add_argument("--app-base-url", help="Optional deployed app base URL for GET /stats")
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--duration-seconds", type=int, default=10)
    parser.add_argument("--poll-seconds", type=int, default=0)
    args = parser.parse_args()

    if not args.api_key:
        print("PSEUDOGRAM_API_KEY is required via --api-key or environment.", file=sys.stderr)
        return 2

    headers = {"X-API-Key": args.api_key}
    base_url = args.base_url.rstrip("/")
    payload = build_start_payload(args.webhook_url, args.count, args.duration_seconds)

    with httpx.Client(timeout=30.0) as client:
        start = client.post(f"{base_url}/v1/simulate/start", json=payload, headers=headers)
        start.raise_for_status()
        start_body = start.json()
        run_id = start_body.get("run_id")
        print({"run_id": run_id, "start_response": start_body})

        if args.poll_seconds:
            time.sleep(args.poll_seconds)

        if run_id:
            truth = client.get(f"{base_url}/v1/simulate/{run_id}/truth", headers=headers)
            truth.raise_for_status()
            print({"truth": truth.json()})

        if args.app_base_url:
            stats = client.get(f"{args.app_base_url.rstrip('/')}/stats")
            stats.raise_for_status()
            print({"stats": stats.json()})

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
