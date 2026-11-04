# VCM — Muse 側 (muse/)

Voice Coach for Muse の「頭脳」側一式。Garmin LiveTrack を監視し、
走行中に音声コーチングを生成して配信キューへ流すプログラム群と、
Muse (エージェント) へ渡す運用手順書を含む。

受信側の iOS アプリはこのリポジトリのルート側にある。アプリがキューを
ポーリングして音声を再生し、再生中は音楽をダッキングする。

## 構成

| ファイル | 役割 |
|---|---|
| `livetrack_poll.py` | LiveTrack API のポーリング。セッション情報と軌跡を取得し、距離・時間・心拍・スプリットを JSON で返す |
| `coach_monitor.py` | 監視ループ本体。文面の組み立て、送信タイミング、終了処理、再送を担う |
| `publish.py` | 発行係。Muse TTS で音声化し、Worker のキューへ上げる (ローカルミラーも保持) |
| `session_watcher.py` | 自動開始チェーンの常駐ウォッチャー。Worker にセッションが届いたら監視を起動 |
| `run_watch.py` | 手動開始用。待機音声を流しながら開始メールを検知し、監視へ渡す |
| `wait_announce.py` | 検知までの待機音声 (run_watch から起動される) |
| `finalize_run.py` | 監視が途中で死んだ場合の復旧。最終ポイントから終了処理を完遂する |
| `worker/` | 配信基盤 (Cloudflare Worker + R2)。キュー・音声・履歴・セッションの置き場 |
| `gas/` | 開始メール検知 (Google Apps Script)。アプリのキックで Gmail を検索し Worker へ渡す |
| `prompts/runbook.md` | Muse へ渡す運用手順書 (文面の型、起動・終了・復旧の段取り) |
| `config.sample.json` | 監視設定の雛形 |
| `comment_stock.sample.json` | コメント素材の雛形 (実運用では自分の言葉に差し替える) |

## セットアップの順序

1. `worker/` を Cloudflare にデプロイする (R2 バケット作成、
   `wrangler secret put UPLOAD_TOKEN`、`wrangler deploy`)。
2. アップロードトークンを Muse のシークレット管理に登録し、
   カスタムコネクタ経由で publish.py / session_watcher.py から使える
   ようにする (コードにトークンは書かない)。
3. `gas/` を設置する (手順は gas/README.md)。
4. iOS アプリに Worker の URL と GAS のキック先を設定する。
5. `config.sample.json` を `coach_config.json` にコピーして自分の
   環境に合わせ、`comment_stock.sample.json` を `comment_stock.json`
   にコピーしてコメントを差し替える。
6. 自動開始を使う場合は session_watcher.py を常駐させる。

## 環境変数

| 変数 | 既定値 | 用途 |
|---|---|---|
| `VCM_WORKER_HOST` | `vcm-queue.example.workers.dev` | Worker のホスト名 |
| `VCM_QUEUE_DIR` | `~/vcm/queue` | publish.py のローカルミラー置き場 |
| `VCM_BASE_DIR` | `~/vcm` | session_watcher.py の状態置き場 |
| `VCM_CONNECTOR_ID` | `custom.vcm-worker-upload` | トークンを供給する Muse カスタムコネクタの ID |
| `VCM_TTS_VOICE` | `avocado_v2:MAI_01` | コーチング音声の声 (Aria) |

## このパッケージに含まれないもの

- 実運用の設定ファイル (`coach_config.json`) とコメント素材の実物
- 実行時状態 (セッション情報、ポーリング状態、使用済みコメント、ログ)
- トークン類 (LiveTrack トークン、アップロードトークン)

これらは .gitignore でも除外している。
