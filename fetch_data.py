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
FRED_SERIES_RELEASE_URL = "https://api.stlouisfed.org/fred/series/release"
FRED_RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"

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


def fred_get(url, params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}",
                                 headers={"User-Agent": "econ-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(api_key, series_id, units, months_back):
    data = fred_get(FRED_URL, {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "units": units,
        "observation_start": start_date(months_back),
    })
    return {
        "series_id": series_id,
        "units": units,
        "observations": [
            {"date": o["date"], "value": float(o["value"])}
            for o in data.get("observations", [])
            if o.get("value") not in (".", "", None)
        ],
    }


def fetch_release_info(api_key, series_id, _memo={}):
    """系列の公開日(直近)と次回公開予定日を返す。取得失敗は None。"""
    rel = fred_get(FRED_SERIES_RELEASE_URL, {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
    })["releases"][0]
    rid = rel["id"]
    if rid in _memo:
        return _memo[rid]
    time.sleep(0.5)
    data = fred_get(FRED_RELEASE_DATES_URL, {
        "release_id": rid,
        "api_key": api_key,
        "file_type": "json",
        "include_release_dates_with_no_data": "true",
        "realtime_end": "9999-12-31",
        "sort_order": "desc",
        "limit": 60,
    })
    today = datetime.date.today().isoformat()
    dates = [d["date"] for d in data.get("release_dates", [])]  # 降順
    info = {
        "name": rel.get("name", ""),
        "last_date": next((d for d in dates if d <= today), None),
        "next_date": min((d for d in dates if d > today), default=None),
    }
    _memo[rid] = info
    return info


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
        if n == 0:
            sys.exit(f"{sid} の観測値が0件 — 異常なので中断します")
        time.sleep(0.5)  # FREDレート制限への配慮
        try:
            release = fetch_release_info(api_key, sid)
            bundle["series"][sid]["release"] = release
            print(f"  {sid}: {n} obs, 公開 {release['last_date']} / 次回 {release['next_date']}")
        except Exception as e:  # 公開日はおまけ情報なので失敗しても続行
            print(f"  {sid}: {n} obs, リリース日取得失敗: {e}")
        time.sleep(0.5)

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
