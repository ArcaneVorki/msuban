#!/usr/bin/env python3
"""
Local Python version of msuban's wallet ban-check fetcher.

Unlike the GitHub Actions version (scripts/fetch.js), this script:
  - Detects HTTP 429 (rate limited) — including a 429 embedded in an
    individual result even when the outer HTTP response is 200 — and stops
    immediately rather than retrying
  - Does NOT fabricate error entries for addresses affected by the
    rate limit — they're simply left out of the output entirely
  - Reports the address index where processing stopped, so you can resume
    later with --start-index once the API is available again
  - Merges resumed runs into the existing output file rather than overwriting,
    so running it multiple times with increasing --start-index values
    builds up one complete dataset

Once a run completes the FULL address list (no stop partway through), it
also updates the same files the GitHub Actions version normally maintains,
so a single `git add data/ && git commit && git push` after a full run is
enough for the dashboard to pick it up correctly:
  - data/latest.json          - today's full snapshot
  - data/history/<date>.json  - same snapshot, dated (for the trend tab)
  - data/manifest.json        - list of dates with history
  - data/ban-history.json     - persistent per-address ban history
                                 (repeat-offender tracking, previously-banned tab)

If a run stops partway through (rate limited, etc.), none of those four
files are touched except data/latest.json itself — resume with
--start-index until a run completes fully before pushing, otherwise the
dashboard's history/manifest/ban-history data would be built from an
incomplete day's results.

Requires the `requests` library:
    pip install requests

Usage:
    python fetch_local.py
    python fetch_local.py --start-index 350
    python fetch_local.py --addresses-file data/addresses.json --delay 60
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

API_URL = "https://api.msu123.com/api/wallet-analysis/ban-check"
BATCH_SIZE = 8
DEFAULT_DELAY_SECONDS = 300  # matches the GitHub Actions version's current delay

DATA_DIR = "data"
DEFAULT_OUTPUT = os.path.join(DATA_DIR, "latest.json")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
MANIFEST_PATH = os.path.join(DATA_DIR, "manifest.json")
BAN_HISTORY_PATH = os.path.join(DATA_DIR, "ban-history.json")

# A plain Python-urllib/requests default User-Agent gets blocked (403) by a
# lot of WAFs/CDNs since it looks like a bot. Presenting a normal browser
# User-Agent avoids that.
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
}


def load_addresses(path):
    with open(path, "r", encoding="utf-8") as f:
        addresses = json.load(f)
    if not isinstance(addresses, list) or len(addresses) == 0:
        print(f"No addresses found in {path} — aborting.", file=sys.stderr)
        sys.exit(1)
    return addresses


def load_existing_output(path):
    """Loads results from a prior run's output file, if resuming."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("results", [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def post_batch(api_url, addresses):
    """POSTs one batch to the ban-check API.
    Returns (results_list, None) on success, or (None, http_status_code) on
    an HTTP error response (4xx/5xx). Raises for non-HTTP failures (network
    issues, timeouts, malformed responses, etc.) — the caller treats those
    the same as a hard stop, just with a different reason string.
    """
    res = requests.post(
        api_url,
        json={"addresses": addresses},
        headers=HEADERS,
        timeout=30,
    )
    if not res.ok:
        return None, res.status_code
    body = res.json()
    return body.get("results", []), None


def find_rate_limited_index(results):
    """Scans a batch's results for a per-address 'error' field indicating a
    429, which the API can embed even when the outer HTTP response is 200.
    Returns the position within this batch of the first such entry, or None
    if the whole batch is clean.
    """
    for i, r in enumerate(results):
        err = r.get("error")
        if err and "429" in str(err):
            return i
    return None


def update_manifest(date):
    manifest = []
    if os.path.exists(MANIFEST_PATH):
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except json.JSONDecodeError:
            manifest = []
    if date not in manifest:
        manifest.append(date)
        manifest.sort()
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def write_history(date, record):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    with open(os.path.join(HISTORY_DIR, f"{date}.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)


def update_ban_history(results, checked_at):
    """Ported line-for-line from fetch.js's updateBanHistory — same logic,
    same semantics. Maintains data/ban-history.json: a persistent per-address
    record of every distinct ban period ever observed, so repeat offenders
    can be told apart from addresses banned once and never again.
    """
    history = {}
    if os.path.exists(BAN_HISTORY_PATH):
        try:
            with open(BAN_HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f)
        except json.JSONDecodeError:
            history = {}

    for r in results:
        addr = r.get("address")
        ban_info = r.get("banInfo") or {}
        is_banned = bool(ban_info.get("banned"))

        if addr not in history:
            history[addr] = {
                "timesBanned": 0,
                "currentlyBanned": False,
                "periods": [],
                "lastCheckedAt": checked_at,
            }
        entry = history[addr]
        entry["lastCheckedAt"] = checked_at

        if is_banned:
            last_period = entry["periods"][-1] if entry["periods"] else None
            # A period is only "new" if the API reports a genuinely different
            # banStartAt. If banStartAt matches the last period we recorded —
            # even one we'd previously marked closed (e.g. a transient
            # unbanned reading in between) — this is the same ban continuing,
            # not a fresh one, so we reopen and update it instead of
            # double-counting it as a repeat offense.
            is_new_period = (
                last_period is None
                or last_period.get("banStartAt") != ban_info.get("banStartAt")
            )
            if is_new_period:
                entry["periods"].append({
                    "banStartAt": ban_info.get("banStartAt"),
                    "banEndAt": ban_info.get("banEndAt"),
                    "isPermanentBan": bool(ban_info.get("isPermanentBan")),
                    "closed": False,
                })
                entry["timesBanned"] += 1
            else:
                last_period["banEndAt"] = ban_info.get("banEndAt") or last_period.get("banEndAt")
                last_period["isPermanentBan"] = bool(ban_info.get("isPermanentBan"))
                last_period["closed"] = False
            entry["currentlyBanned"] = True
        else:
            last_period = entry["periods"][-1] if entry["periods"] else None
            if last_period and not last_period.get("closed"):
                last_period["closed"] = True
            entry["currentlyBanned"] = False

    with open(BAN_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)

    return history


def main():
    parser = argparse.ArgumentParser(
        description="Local wallet ban-check fetcher — stops cleanly on HTTP 429"
    )
    parser.add_argument(
        "--addresses-file", default="data/addresses.json",
        help="Path to the address list JSON (default: data/addresses.json)",
    )
    parser.add_argument(
        "--output", default=DEFAULT_OUTPUT,
        help=f"Path to write/merge results into (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--delay", type=float, default=DEFAULT_DELAY_SECONDS,
        help=f"Seconds to wait between batch calls (default: {DEFAULT_DELAY_SECONDS})",
    )
    parser.add_argument(
        "--start-index", type=int, default=0,
        help="Address index to resume from (default: 0, i.e. start from the beginning)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=BATCH_SIZE,
        help=f"Addresses per API call (default: {BATCH_SIZE})",
    )
    parser.add_argument(
        "--api-url", default=API_URL,
        help="Override the API URL (mainly useful for local testing)",
    )
    args = parser.parse_args()

    addresses = load_addresses(args.addresses_file)
    total = len(addresses)

    if args.start_index >= total:
        print(f"start-index {args.start_index} is past the end of the address list ({total}). Nothing to do.")
        sys.exit(0)

    existing_results = load_existing_output(args.output) if args.start_index > 0 else []
    new_results = []

    remaining = addresses[args.start_index:]
    batches = [remaining[i:i + args.batch_size] for i in range(0, len(remaining), args.batch_size)]

    stopped_at_index = None
    stop_reason = None

    print(f"Checking {len(remaining)} addresses ({len(batches)} batches) starting at index {args.start_index}...")

    for batch_num, batch in enumerate(batches):
        batch_start_index = args.start_index + batch_num * args.batch_size

        try:
            results, http_status = post_batch(args.api_url, batch)
        except Exception as e:
            print(f"\nBatch at index {batch_start_index} failed with a non-HTTP error: {e}")
            stopped_at_index = batch_start_index
            stop_reason = f"error: {e}"
            break

        if http_status == 429:
            print(f"\nHTTP 429 (rate limited) at index {batch_start_index}. Stopping — this batch is NOT included in the output.")
            stopped_at_index = batch_start_index
            stop_reason = "HTTP 429"
            break
        elif http_status is not None:
            print(f"\nHTTP {http_status} at index {batch_start_index}. Stopping — this batch is NOT included in the output.")
            stopped_at_index = batch_start_index
            stop_reason = f"HTTP {http_status}"
            break

        rate_limited_pos = find_rate_limited_index(results)
        if rate_limited_pos is not None:
            # The batch itself returned HTTP 200, but this specific address's
            # result carries an embedded 429 error. Keep everything before
            # it (those genuinely succeeded), drop it and everything after
            # in this batch, and stop here.
            good_results = results[:rate_limited_pos]
            new_results.extend(good_results)
            stopped_at_index = batch_start_index + rate_limited_pos
            stop_reason = "HTTP 429 (embedded in result)"
            print(
                f"\nHTTP 429 embedded in result at index {stopped_at_index} "
                f"(address {results[rate_limited_pos].get('address')}). "
                f"Stopping — this address and any after it in the batch are NOT included in the output."
            )
            break

        new_results.extend(results)
        checked_so_far = args.start_index + batch_num * args.batch_size + len(batch)
        print(f"  ...{checked_so_far}/{total} addresses checked")

        if batch_num < len(batches) - 1:
            time.sleep(args.delay)

    all_results = existing_results + new_results
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    record = {
        "date": today,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "totalAddresses": len(all_results),
        "totalInList": total,
        "results": all_results,
    }

    if stopped_at_index is not None:
        record["stoppedAtIndex"] = stopped_at_index
        record["stopReason"] = stop_reason

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)

    print(f"\nSaved {len(all_results)}/{total} addresses to {args.output}")

    if stopped_at_index is not None:
        print(f"\nStopped at index {stopped_at_index} ({stop_reason}).")
        print("To resume once the API is available again, run:")
        print(f"  python {sys.argv[0]} --start-index {stopped_at_index}")
        print(
            "\ndata/history, data/manifest.json, and data/ban-history.json were NOT "
            "updated — this run is incomplete. Resume and re-run until it completes "
            "fully before pushing, otherwise the dashboard's trend/history data "
            "would be built from a partial day."
        )
    else:
        print("All addresses checked successfully.")
        write_history(today, record)
        update_manifest(today)
        update_ban_history(all_results, record["checkedAt"])
        print(
            f"\nUpdated data/history/{today}.json, data/manifest.json, and "
            "data/ban-history.json. You're good to push now:"
        )
        print("  git add data/")
        print(f'  git commit -m "Manual ban check: {today}"')
        print("  git push")


if __name__ == "__main__":
    main()
