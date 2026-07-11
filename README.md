# 米国経済指標ダッシュボード

インフレ動向・雇用状況・賃金インフレを7指標に絞って一目で把握するローカルダッシュボード。
データはFRED（セントルイス連銀）APIから自動取得します。

## セットアップ（初回のみ）

1. FRED APIキーを無料取得: https://fredaccount.stlouisfed.org/apikeys
   （アカウント作成 → 「Request API Key」で即発行されます）
2. `config.example.json` を `config.json` にコピーして、取得したキーを記入:
   ```json
   { "fred_api_key": "ここにキーを貼る" }
   ```
   ※ 環境変数 `FRED_API_KEY` でも可

## 起動

```bash
python3 serve.py
```

ブラウザが自動で開きます（http://localhost:8390/）。停止は Ctrl+C。

- データは開くたびに自動取得（1時間キャッシュ。「最新に更新」ボタンで強制再取得）
- オフライン時やAPI障害時は前回キャッシュを表示
- カードをクリックするとFREDの該当系列ページが開きます
- 各カードに直近の公開日と次回の公開予定日（FREDのリリースカレンダー由来）を表示

## 掲載指標と選定理由

各カテゴリ「軸となる指標 + 速報性の補完」の構成に絞っています。

| カテゴリ | 指標 | 系列ID | 役割 |
|---|---|---|---|
| インフレ | コアPCE | PCEPILFE | FRBの2%目標の物差し（軸） |
| インフレ | コアCPI | CPILFESL | 月次で速い速報役 |
| 雇用 | 失業率 | UNRATE | ヘッドラインの軸 |
| 雇用 | 非農業部門雇用者数 | PAYEMS | 毎月の注目イベント（前月差表示） |
| 雇用 | 新規失業保険申請 | ICSA | 週次で最速の先行指標 |
| 賃金 | ECI賃金 | ECIWAG | 構成変化に左右されない質重視の軸（四半期） |
| 賃金 | 平均時給（民間） | CES0500000003 | 月次の速報役（ECIの四半期空白を埋める） |

総合PCE・トリム平均/粘着/中央値CPI・U-6・労働参加率・JOLTSなどは役割が重複するか
速報性に欠けるためホームからは除外（将来、カテゴリ別詳細ページに追加予定）。

前年比・前月差はFRED APIの `units` パラメータ（`pc1`/`chg`）でサーバー側計算。

## 色の意味

緑 = インフレ沈静化・雇用改善の方向、赤 = その逆。
賃金指標は「賃金インフレ圧力」の観点で色付け（伸び鈍化 = 緑）。

## スマホからの閲覧（GitHub Pages）

GitHub Actions が6時間ごとにFREDからデータを取得し、GitHub Pages に静的ページとして
デプロイします。PCの電源に関係なく、スマホのブラウザでURLを開くだけで閲覧できます。

- APIキーはリポジトリの Secrets（`FRED_API_KEY`）に保存され、公開されません
- `index.html` は Pages 上では静的 `data.json` を読み、ローカル（serve.py）では
  ライブAPIを使う二段構え
- 手動更新: リポジトリの Actions タブ → "Update data and deploy" → Run workflow

## 指標更新のプッシュ通知（ntfy.sh）

指標に**新しい観測値が出たときだけ**、スマホに無料でプッシュ通知が届きます
（ワークフローは1日4回動きますが、値が変わらなければ通知しません）。

仕組み: Actions がデプロイ前に公開中の `data.json` を保存し、デプロイ後に
`notify_update.py` が新旧の最新観測日を比較 → 更新された指標をまとめて
ntfy.sh にPOST。通知タップでダッシュボードが開きます。

セットアップ（スマホ側・初回のみ）:

1. App Store / Google Play で「ntfy」アプリをインストール
2. アプリで「＋」→ リポジトリ Secrets の `NTFY_TOPIC` と同じトピック名を購読

- トピック名は推測されにくいランダム文字列にして Secrets（`NTFY_TOPIC`）に保存
  （ntfy.sh はトピック名を知っていれば誰でも購読できるため、実質パスワード扱い）
- 通知の失敗はデプロイに影響しません（notify_update.py は常に exit 0）

## ファイル構成

```
serve.py             # ローカル用ミニサーバー（Python標準ライブラリのみ、依存なし）
fetch_data.py        # GitHub Actions用データ取得スクリプト → data.json 生成
index.html           # ダッシュボード本体（自己完結・外部CDNなし）
config.json          # あなたのAPIキー（各自作成・コミットしない）
config.example.json  # 設定のひな形
notify_update.py     # 指標更新時のntfy.shプッシュ通知（GitHub Actions用）
cache/               # APIレスポンスのキャッシュ（自動生成、削除可）
.github/workflows/update-data.yml  # 6時間ごとの自動更新＆デプロイ
```
