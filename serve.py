#!/usr/bin/env python3
"""FRED経済指標ダッシュボード ミニサーバー。

静的ページ配信 + FRED APIプロキシ + 1時間キャッシュ。
依存は Python 標準ライブラリのみ。

起動:  python3 serve.py
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler

from fetch_data import fetch_ff_futures, fetch_nowcast, fetch_usdjpy

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(BASE_DIR, "cache")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
PORT = 8390
CACHE_TTL = 3600  # 秒

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_SERIES_RELEASE_URL = "https://api.stlouisfed.org/fred/series/release"
FRED_RELEASE_DATES_URL = "https://api.stlouisfed.org/fred/release/dates"

# 開放プロキシ化を防ぐため、取得できる系列は掲載指標に限定する
ALLOWED_SERIES = {
    "PCEPILFE",        # コアPCE
    "CPILFESL",        # コアCPI
    "UNRATE",          # 失業率
    "PAYEMS",          # 非農業部門雇用者数
    "ICSA",            # 新規失業保険申請
    "ECIWAG",          # ECI賃金
    "CES0500000003",   # 平均時給
    # 予測セクション用
    "EFFR",            # 実効FF金利
    "NROU",            # 自然失業率（CBO）
    "DGS1",            # 1年債利回り
    "DGS2",            # 2年債利回り
    "EXPINF1YR",       # 1年期待インフレ
    "SP500",           # S&P500
    "VIXCLS",          # VIX
    "BAMLH0A0HYM2",    # ハイイールド債OAS
    "NIKKEI225",       # 日経225
}
ALLOWED_UNITS = {"lin", "pc1", "chg"}


def load_api_key():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            key = json.load(f).get("fred_api_key", "").strip()
            if key and key != "YOUR_API_KEY_HERE":
                return key
    except (OSError, json.JSONDecodeError):
        pass
    return os.environ.get("FRED_API_KEY", "").strip() or None


def cache_path(series_id, units):
    return os.path.join(CACHE_DIR, f"{series_id}_{units}.json")


def read_cache(series_id, units):
    try:
        with open(cache_path(series_id, units), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def write_cache(series_id, units, payload):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path(series_id, units), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def fred_get(url, params):
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}",
                                 headers={"User-Agent": "econ-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_fred(api_key, series_id, units, start):
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "units": units,
        "observation_start": start,
    }
    if series_id == "NROU":
        # CBO推計は将来四半期の予測値も返すため、今日までに限定する
        params["observation_end"] = time.strftime("%Y-%m-%d")
    data = fred_get(FRED_URL, params)
    observations = [
        {"date": o["date"], "value": float(o["value"])}
        for o in data.get("observations", [])
        if o.get("value") not in (".", "", None)
    ]
    return {
        "series_id": series_id,
        "units": units,
        "fetched_at": int(time.time()),
        "observations": observations,
    }


# release_id → {"fetched_at", "info"} のメモ。Employment Situation を
# 共有する3系列（UNRATE/PAYEMS/CES0500000003）の重複呼び出しを避ける。
_release_memo = {}


def fetch_release_info(api_key, series_id):
    """系列の公開日(直近)と次回公開予定日を返す。"""
    rel = fred_get(FRED_SERIES_RELEASE_URL, {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
    })["releases"][0]
    rid = rel["id"]
    memo = _release_memo.get(rid)
    if memo and time.time() - memo["fetched_at"] < CACHE_TTL:
        return memo["info"]
    data = fred_get(FRED_RELEASE_DATES_URL, {
        "release_id": rid,
        "api_key": api_key,
        "file_type": "json",
        "include_release_dates_with_no_data": "true",
        "realtime_end": "9999-12-31",
        "sort_order": "desc",
        "limit": 60,
    })
    today = time.strftime("%Y-%m-%d")
    dates = [d["date"] for d in data.get("release_dates", [])]  # 降順
    info = {
        "name": rel.get("name", ""),
        "last_date": next((d for d in dates if d <= today), None),
        "next_date": min((d for d in dates if d > today), default=None),
    }
    _release_memo[rid] = {"fetched_at": time.time(), "info": info}
    return info


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=BASE_DIR, **kwargs)

    def log_message(self, fmt, *args):
        pass  # アクセスログは抑制（エラーは send_json 側で表示）

    def send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/series":
            self.handle_series(urllib.parse.parse_qs(parsed.query))
        elif parsed.path == "/api/nowcast":
            self.handle_nowcast(urllib.parse.parse_qs(parsed.query))
        elif parsed.path == "/api/usdjpy":
            self.handle_usdjpy(urllib.parse.parse_qs(parsed.query))
        elif parsed.path == "/api/fffutures":
            self.handle_fffutures(urllib.parse.parse_qs(parsed.query))
        elif parsed.path == "/data.json":
            # ローカルでは常にライブAPIを使わせる（古い静的JSONの誤読防止）
            self.send_json(404, {"error": "not_found"})
        elif parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            super().do_GET()

    def handle_nowcast(self, qs):
        """クリーブランド連銀ナウキャストのプロキシ（先方はCORS非対応）。"""
        force = (qs.get("refresh") or ["0"])[0] == "1"
        cached = read_cache("nowcast", "cle")
        if cached and not force and time.time() - cached["fetched_at"] < CACHE_TTL:
            return self.send_json(200, cached)
        try:
            nowcast = fetch_nowcast()
            if nowcast is None:
                raise ValueError("parse failed")
            nowcast["fetched_at"] = int(time.time())
            write_cache("nowcast", "cle", nowcast)
            return self.send_json(200, nowcast)
        except Exception:
            if cached:
                cached["stale"] = True
                return self.send_json(200, cached)
            return self.send_json(502, {"error": "nowcast_unavailable"})

    def handle_usdjpy(self, qs):
        """ドル円（ECB参照レート）のプロキシ。形はFRED系列と同じ。"""
        force = (qs.get("refresh") or ["0"])[0] == "1"
        cached = read_cache("USDJPY", "ecb")
        if cached and not force and time.time() - cached["fetched_at"] < CACHE_TTL:
            return self.send_json(200, cached)
        try:
            payload = fetch_usdjpy()
            if payload is None:
                raise ValueError("no data")
            payload["fetched_at"] = int(time.time())
            write_cache("USDJPY", "ecb", payload)
            return self.send_json(200, payload)
        except Exception:
            if cached:
                cached["stale"] = True
                return self.send_json(200, cached)
            return self.send_json(502, {"error": "usdjpy_unavailable"})

    def handle_fffutures(self, qs):
        """FF金利先物（Yahoo Finance経由）のプロキシ。"""
        force = (qs.get("refresh") or ["0"])[0] == "1"
        cached = read_cache("fffutures", "yahoo")
        if cached and not force and time.time() - cached["fetched_at"] < CACHE_TTL:
            return self.send_json(200, cached)
        try:
            payload = fetch_ff_futures()
            if payload is None:
                raise ValueError("too few contracts")
            payload["fetched_at"] = int(time.time())
            write_cache("fffutures", "yahoo", payload)
            return self.send_json(200, payload)
        except Exception:
            if cached:
                cached["stale"] = True
                return self.send_json(200, cached)
            return self.send_json(502, {"error": "fffutures_unavailable"})

    def handle_series(self, qs):
        series_id = (qs.get("id") or [""])[0].upper()
        units = (qs.get("units") or ["lin"])[0]
        start = (qs.get("start") or ["2000-01-01"])[0]
        force = (qs.get("refresh") or ["0"])[0] == "1"

        if series_id not in ALLOWED_SERIES:
            return self.send_json(400, {"error": "unknown_series", "id": series_id})
        if units not in ALLOWED_UNITS:
            return self.send_json(400, {"error": "unknown_units"})
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start):
            return self.send_json(400, {"error": "bad_start_date"})

        cached = read_cache(series_id, units)
        if cached and not force and time.time() - cached["fetched_at"] < CACHE_TTL:
            cached["from_cache"] = True
            return self.send_json(200, cached)

        api_key = load_api_key()
        if not api_key:
            if cached:
                cached["from_cache"] = True
                cached["stale"] = True
                return self.send_json(200, cached)
            return self.send_json(503, {"error": "no_api_key"})

        try:
            payload = fetch_fred(api_key, series_id, units, start)
            try:
                payload["release"] = fetch_release_info(api_key, series_id)
            except Exception:
                pass  # 公開日はおまけ情報。失敗しても観測値は返す
            write_cache(series_id, units, payload)
            return self.send_json(200, payload)
        except urllib.error.HTTPError as e:
            if e.code == 400:
                # FREDはAPIキー不正でも400を返す
                if cached:
                    cached["from_cache"] = True
                    cached["stale"] = True
                    return self.send_json(200, cached)
                return self.send_json(502, {"error": "fred_rejected",
                                            "detail": "APIキーが無効か、リクエストが不正です"})
            return self.send_json(502, {"error": "fred_http_error", "code": e.code})
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if cached:
                cached["from_cache"] = True
                cached["stale"] = True
                return self.send_json(200, cached)
            return self.send_json(502, {"error": "network_error", "detail": str(e)})


def main():
    if not load_api_key():
        print("=" * 60)
        print("FRED APIキーが見つかりません。")
        print("1. https://fredaccount.stlouisfed.org/apikeys で無料取得")
        print("2. config.example.json を config.json にコピーしてキーを記入")
        print("   （または環境変数 FRED_API_KEY を設定）")
        print("キー未設定でもページは開けますが、データは取得できません。")
        print("=" * 60)

    server = HTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}/"
    print(f"経済指標ダッシュボード: {url}  (Ctrl+C で停止)")
    threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n停止しました。")


if __name__ == "__main__":
    main()
