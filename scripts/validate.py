#!/usr/bin/env python3
"""Validator run in CI after every snapshot that exits non-zero on corruption."""
import json, sys, os, datetime

def read_json(path):
    with open(path) as f:
        return json.load(f)

def validate_day(path, prev_path=None):
    try:
        snap = read_json(path)
    except Exception as e:
        print(f"FAIL: {path} is not valid JSON ({e})")
        return False

    if "filters" not in snap or "wallstreetbets" not in snap["filters"]:
        print(f"FAIL: {path} schema drift: missing filters.wallstreetbets")
        return False
        
    rows = snap["filters"]["wallstreetbets"]
    if not isinstance(rows, list):
        print(f"FAIL: {path} schema drift: wallstreetbets is not a list")
        return False

    if len(rows) < 250 or len(rows) > 600:
        print(f"FAIL: {path} implausible payload: row count {len(rows)} far from expected (250-600)")
        return False

    # One row per ticker. The board is assembled from five paginated calls and its tail is
    # a mass of ties, so a ticker can be returned by two of them at different ranks — five
    # days of the archive carry it, and every duplicate is also a board slot that no call
    # returned at all. The archiver dedupes now; this is what notices if that stops
    # working, or if the next source has the same shape.
    tickers = [r.get("ticker") for r in rows]
    dupes = {t for t in tickers if tickers.count(t) > 1}
    if dupes:
        print(f"FAIL: {path} has {len(tickers) - len(set(tickers))} duplicate rows "
              f"({sorted(dupes)[:6]}) — pagination returned a ticker twice, which also "
              f"means that many board positions are missing")
        return False

    has_rank_1 = False
    for i, r in enumerate(rows):
        for k in ("ticker", "mentions", "rank"):
            if k not in r:
                print(f"FAIL: {path} schema drift: missing '{k}' on row {i}")
                return False
        if r["rank"] == 1:
            has_rank_1 = True
            if r["mentions"] == 0:
                print(f"FAIL: {path} implausible payload: rank 1 has 0 mentions")
                return False

    if not has_rank_1:
        print(f"FAIL: {path} implausible payload: no rank 1 found")
        return False

    if prev_path and os.path.exists(prev_path):
        with open(path, "rb") as f1, open(prev_path, "rb") as f2:
            if f1.read() == f2.read():
                print(f"FAIL: {path} is byte-identical to {prev_path} (API cache bug)")
                return False

    return True

def validate_prices(latest_archive_date, prices_path):
    if not os.path.exists(prices_path):
        print(f"FAIL: {prices_path} missing")
        return False
        
    prices = read_json(prices_path)
    ends = []
    for v in prices.values():
        if v and "d" in v and v["d"]:
            ends.append(v["d"][-1])
            
    if not ends:
        print(f"FAIL: {prices_path} has no price data")
        return False
        
    newest = max(ends)
    
    # Check if newest price is more than 3 days behind latest_archive_date
    d1 = datetime.date.fromisoformat(latest_archive_date)
    d2 = datetime.date.fromisoformat(newest)
    if (d1 - d2).days > 3:
        print(f"FAIL: {prices_path} prices ({newest}) are >3 days behind archive ({latest_archive_date})")
        return False
        
    return True

def main():
    audit_mode = "--audit" in sys.argv
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    archive_dir = os.path.join(root, "data", "apewisdom")
    prices_file = os.path.join(root, "data", "prices.json")
    
    files = sorted([f for f in os.listdir(archive_dir) if f.endswith(".json")])
    if not files:
        print("FAIL: No archive files found")
        sys.exit(1)
        
    dates = [f[:-5] for f in files]
    
    # Check dates order and gaps
    fails = False
    for i in range(1, len(dates)):
        if dates[i] <= dates[i-1]:
            print(f"FAIL: Dates out of order or duplicate: {dates[i-1]} -> {dates[i]}")
            fails = True
        
    if audit_mode:
        print(f"Auditing {len(files)} historical snapshots...")
        for i in range(len(files)):
            prev = os.path.join(archive_dir, files[i-1]) if i > 0 else None
            curr = os.path.join(archive_dir, files[i])
            if not validate_day(curr, prev):
                fails = True
    else:
        # Just validate the latest day
        latest = files[-1]
        prev = files[-2] if len(files) > 1 else None
        curr_path = os.path.join(archive_dir, latest)
        prev_path = os.path.join(archive_dir, prev) if prev else None
        if not validate_day(curr_path, prev_path):
            fails = True

    if not validate_prices(dates[-1], prices_file):
        fails = True

    if fails:
        print("Validation FAILED")
        sys.exit(1)
    else:
        print("Validation PASSED")
        sys.exit(0)

if __name__ == "__main__":
    main()
