#!/usr/bin/env python3
"""指標の新値が出たときだけ ntfy.sh にプッシュ通知を送る（GitHub Actions用）。

デプロイ前の公開 data.json（旧）と今回生成した data.json（新）を比較し、
最新観測日が進んだ系列があれば1通にまとめて通知する。

使い方:  NTFY_TOPIC=xxx python3 notify_update.py --new _site/data.json --old prev_data.json

失敗してもデプロイを壊さないよう、常に exit 0 で終わる。
"""
import argparse
import json
import os
import sys
import urllib.request

NTFY_URL = "https://ntfy.sh"
DASHBOARD_URL = "https://takuma41n.github.io/us-econ-dashboard/"

# index.html の SERIES 定義と対応（表示名・整形・頻度）
SERIES_INFO = {
    "PCEPILFE":      ("コアPCE",        "pct",   "m"),
    "CPILFESL":      ("コアCPI",        "pct",   "m"),
    "UNRATE":        ("失業率",         "pct",   "m"),
    "PAYEMS":        ("非農業雇用",     "man10", "m"),
    "ICSA":          ("新規失業保険申請", "man",   "w"),
    "ECIWAG":        ("ECI賃金",        "pct",   "q"),
    "CES0500000003": ("平均時給",       "pct",   "m"),
}


def fmt_value(fmt, v):
    if fmt == "man10":   # 千人 → 万人（前月差なので符号付き）
        x = round(v / 10, 1)
        return f"{x:+g}万人"
    if fmt == "man":     # 件 → 万件
        return f"{round(v / 10000, 1):g}万件"
    return f"{round(v, 1):g}%"


def fmt_date(freq, date):
    return date if freq == "w" else date[:7]  # 週次は日まで、月次/四半期は年月


def latest(series_entry):
    obs = series_entry.get("observations") or []
    return obs[-1] if obs else None


def build_message(old_bundle, new_bundle):
    lines = []
    for sid, (name, fmt, freq) in SERIES_INFO.items():
        new_s = (new_bundle.get("series") or {}).get(sid)
        old_s = (old_bundle.get("series") or {}).get(sid)
        if not new_s or not old_s:
            continue
        nl, ol = latest(new_s), latest(old_s)
        if not nl or not ol or nl["date"] <= ol["date"]:
            continue
        lines.append(f"{name}: {fmt_value(fmt, nl['value'])}"
                     f"（{fmt_date(freq, nl['date'])}、前回 {fmt_value(fmt, ol['value'])}）")
    return "\n".join(lines)


def send(topic, message, title="米経済指標 更新", tags=("bar_chart",)):
    body = json.dumps({
        "topic": topic,
        "title": title,
        "message": message,
        "click": DASHBOARD_URL,
        "tags": list(tags),
    }).encode("utf-8")
    req = urllib.request.Request(NTFY_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", dest="new_path", default="_site/data.json")
    ap.add_argument("--old", dest="old_path", default="prev_data.json")
    args = ap.parse_args()

    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC 未設定 — 通知をスキップ")
        return

    try:
        with open(args.old_path, encoding="utf-8") as f:
            old_bundle = json.load(f)
    except (OSError, json.JSONDecodeError):
        print("旧 data.json が読めない（初回デプロイ等）— 通知をスキップ")
        return

    with open(args.new_path, encoding="utf-8") as f:
        new_bundle = json.load(f)

    message = build_message(old_bundle, new_bundle)
    if not message:
        print("新しい観測値なし — 通知しない")
        return

    try:
        send(topic, message)
        print("通知を送信:\n" + message)
    except Exception as e:  # 通知失敗でもデプロイは成功扱い
        print(f"ntfy送信失敗（デプロイには影響なし）: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
