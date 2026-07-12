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

# 予測セクション用の系列（カード表示なし・リリース日不要）
# index.html の FORECAST_SERIES 定義と対応
FORECAST_SERIES = [
    ("PCEPILFE", "lin", 30),      # コアPCE指数水準 → 3m/6m年率換算
    ("CPILFESL", "lin", 30),      # コアCPI指数水準 → 同上
    ("EFFR", "lin", 3),           # 実効FF金利
    ("NROU", "lin", 18),          # 自然失業率（CBO・四半期）
    ("DGS1", "lin", 3),           # 1年債利回り
    ("DGS2", "lin", 3),           # 2年債利回り
    ("EXPINF1YR", "lin", 6),      # 1年期待インフレ（クリーブランド連銀モデル）
    ("SP500", "lin", 15),         # S&P500（125日MA・52週高値の計算に約14ヶ月必要）
    ("VIXCLS", "lin", 2),         # VIX
    ("BAMLH0A0HYM2", "lin", 2),   # ハイイールド債OAS（信用ストレス）
    ("NIKKEI225", "lin", 15),     # 日経225（125日MA・52週高値・実現ボラ用）
]

# ドル円（日経センチメントの円成分）。FREDのDEXJPUSは週次公表で約1週間
# 遅れるため、ECB公式参照レートを日次で返す frankfurter.dev を使う。
FRANKFURTER_URL = "https://api.frankfurter.dev/v1"

# クリーブランド連銀インフレ・ナウキャスト（公開JSON・認証不要）
NOWCAST_URLS = {
    "mom": "https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/nowcast_month.json?sc_lang=en",
    "yoy": "https://www.clevelandfed.org/-/media/files/webcharts/inflationnowcasting/nowcast_year.json?sc_lang=en",
}
NOWCAST_MEASURES = {
    "CPI Inflation": "cpi",
    "Core CPI Inflation": "cpi_core",
    "PCE Inflation": "pce",
    "Core PCE Inflation": "pce_core",
}


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
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "units": units,
        "observation_start": start_date(months_back),
    }
    if series_id == "NROU":
        # CBO推計は将来四半期の予測値も返すため、今日までに限定する
        params["observation_end"] = datetime.date.today().isoformat()
    data = fred_get(FRED_URL, params)
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


def _last_value(dataset_entry):
    """FusionCharts系列の最後の非空値を float で返す。なければ None。"""
    for point in reversed(dataset_entry.get("data") or []):
        v = point.get("value")
        if v not in ("", None):
            try:
                return float(v)
            except ValueError:
                return None
    return None


def _parse_nowcast_charts(charts, kind, months):
    """クリーブランド連銀ナウキャストJSON（月別チャートの配列）を months に集約する。

    kind は "mom"（前月比）か "yoy"（前年比）。直近4ヶ月分だけ見る。
    """
    for chart in charts[-4:]:
        sub = chart.get("chart", {}).get("subcaption", "")  # 例 "2026-7"
        try:
            y, m = sub.split("-")
            month = f"{int(y):04d}-{int(m):02d}"
        except ValueError:
            continue
        entry = months.setdefault(month, {})
        for ds in chart.get("dataset", []):
            name = ds.get("seriesname", "")
            actual = name.startswith("Actual ")
            key = NOWCAST_MEASURES.get(name.removeprefix("Actual "))
            if key is None:
                continue
            v = _last_value(ds)
            if v is None:
                continue
            measure = entry.setdefault(key, {})
            measure["actual_" + kind if actual else kind] = round(v, 2)


def fetch_nowcast():
    """クリーブランド連銀のインフレ・ナウキャストを取得して整形する。

    返り値: {"as_of": "YYYY-MM-DD", "months": [{"month": "YYYY-MM",
             "cpi": {"mom": x, "yoy": y, ...}, "cpi_core": ..., ...}]}
    取得・解析に失敗したら None（呼び出し側でフォールバック）。
    """
    months = {}
    as_of = None
    for kind, url in NOWCAST_URLS.items():
        req = urllib.request.Request(url, headers={
            # サイト側がスクリプトUAを弾くことがあるためブラウザ相当を名乗る
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            charts = json.loads(resp.read().decode("utf-8"))
        if not isinstance(charts, list) or not charts:
            return None
        comment = charts[-1].get("chart", {}).get("_comment", "")  # 例 "2026-07-10 00:00"
        as_of = comment.split(" ")[0] or as_of
        _parse_nowcast_charts(charts, kind, months)
    if not months:
        return None
    return {
        "as_of": as_of,
        "source": "Federal Reserve Bank of Cleveland, Inflation Nowcasting",
        "months": [dict(month=k, **v) for k, v in sorted(months.items())],
    }


def fetch_usdjpy():
    """ドル円の直近約2ヶ月をFRED系列と同じ形で返す。失敗時は None。"""
    start = (datetime.date.today() - datetime.timedelta(days=65)).isoformat()
    req = urllib.request.Request(
        f"{FRANKFURTER_URL}/{start}..?base=USD&symbols=JPY",
        headers={"User-Agent": "econ-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        rates = json.loads(resp.read().decode("utf-8")).get("rates", {})
    obs = [{"date": d, "value": float(v["JPY"])}
           for d, v in sorted(rates.items()) if "JPY" in v]
    if not obs:
        return None
    return {"series_id": "USDJPY", "units": "lin", "observations": obs}


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

    # 予測セクション用の系列（units違いの再取得があるため別名前空間に格納）
    bundle["forecast_series"] = {}
    for sid, units, months_back in FORECAST_SERIES:
        bundle["forecast_series"][sid] = fetch(api_key, sid, units, months_back)
        n = len(bundle["forecast_series"][sid]["observations"])
        print(f"  [forecast] {sid}: {n} obs")
        if n == 0:
            sys.exit(f"{sid} の観測値が0件 — 異常なので中断します")
        time.sleep(0.5)

    # ドル円（失敗してもビルドは止めない。クライアントがHY OASにフォールバック）
    try:
        usdjpy = fetch_usdjpy()
        if usdjpy:
            bundle["forecast_series"]["USDJPY"] = usdjpy
            print(f"  [forecast] USDJPY: {len(usdjpy['observations'])} obs "
                  f"(ECB, latest {usdjpy['observations'][-1]['value']})")
        else:
            print("  [forecast] USDJPY: 取得0件（円成分はHY OASで代替表示）")
    except Exception as e:
        print(f"  [forecast] USDJPY: 取得失敗: {e}（円成分はHY OASで代替表示）")

    # クリーブランド連銀ナウキャスト（失敗してもビルドは止めない）
    try:
        bundle["nowcast"] = fetch_nowcast()
        if bundle["nowcast"]:
            print(f"  [nowcast] as of {bundle['nowcast']['as_of']}, "
                  f"{len(bundle['nowcast']['months'])} months")
        else:
            print("  [nowcast] 解析失敗（フォールバック表示になります）")
    except Exception as e:
        bundle["nowcast"] = None
        print(f"  [nowcast] 取得失敗: {e}（フォールバック表示になります）")

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(bundle, f, ensure_ascii=False)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
