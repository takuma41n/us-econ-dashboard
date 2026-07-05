#!/usr/bin/env python3
"""FREDから全指標を取得して data.json を生成する（GitHub Actions用）。

ローカルの serve.py はライブプロキシで動くのでこのスクリプトは不要。
GitHub Pages 版はこの出力（静的JSON）を読む。

使い方:  FRED_API_KEY=xxx python3 fetch_data.py --out _site/data.json
"""
import argparse
import datetime
import json
import os
import sys
import time
import urllib.parse
import urllib.request

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"

# (series_id, units, 遡り月数) — index.html の SERIES 定義と対応
SERIES = [
    ("PCEPILFE", "pc1", 30),
    ("CPILFESL", "pc1", 30),
    ("UNRATE", "lin", 30),
    ("PAYEMS", "chg", 30),
    ("ICSA", "lin", 14),
    ("ECIWAG", "pc1", 42),
    ("CES0500000003", "pc1", 30),
]


def load_api_key():
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "config.json"), encoding="utf-8") as f:
            key = json.load(f).get("fred_api_key", "").strip()
            if key and key != "YOUR_API_KEY_HERE":
                return key
    except (OSError, json.JSONDecodeError):
        pass
    return None


def start_date(months_back):
    d = datetime.date.today()
    y, m = d.year, d.month - months_back
    while m <= 0:
        y -= 1
        m += 12
    return f"{y:04d}-{m:02d}-01"


def fetch(api_key, series_id, units, months_back):
    params = urllib.parse.urlencode({
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "units": units,
        "observation_start": start_date(months_back),
    })
    req = urllib.request.Request(f"{FRED_URL}?{params}",
                                 headers={"User-Agent": "econ-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return {
        "series_id": series_id,
        "units": units,
        "observations": [
            {"date": o["date"], "value": float(o["value"])}
            for o in data.get("observations", [])
            if o.get("value") not in (".", "", None)
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data.json")
    args = ap.parse_args()

    api_key = load_api_key()
    if not api_key:
        sys.exit("FRED_API_KEY が設定されていません")

    bundle = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
                        .isoformat(timespec="seconds"),
        "series": {},
    }
    for sid, units, months_back in SERIES:
        bundle["series"][sid] = fetch(api_key, sid, units, months_back)
        n = len(bundle["series"][sid]["observations"])
        print(f"  {sid}: {n} obs")
        if n == 0:
            sys.exit(f"{sid} の観測値が0件 — 異常なので中断します")
        time.sleep(0.5)  # FREDレート制限への配慮

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
