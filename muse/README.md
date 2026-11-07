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
| `session_watcher.py` | `--once`で開始準備と状態反映。常駐モードはロールバック用に保持 |
| `run_watch.py` | 手動開始用。待機音声を流しながら開始メールを検知し、監視へ渡す |
| `wait_announce.py` | 検知までの待機音声 (run_watch から起動される) |
| `finalize_run.py` | 監視が途中で死んだ場合の復旧。最終ポイントから終了処理を完遂する |
| `../worker/` | 配信基盤 (Cloudflare Worker + R2)。キュー・音声・履歴・セッションの置き場（リポジトリルートに配置） |
| `gas/` | 開始メール検知 (Google Apps Script)。アプリのキックで Gmail を検索し Worker へ渡す |
| `prompts/runbook.md` | Muse へ渡す運用手順書 (文面の型、起動・終了・復旧の段取り) |
| `config.sample.json` | 監視設定の雛形 |
| `comment_stock.sample.json` | コメント素材の雛形 (実運用では自分の言葉に差し替える) |

## セットアップの順序

1. リポジトリルートの `worker/` を Cloudflare にデプロイする (R2 バケット作成、
   `wrangler secret put UPLOAD_TOKEN`、`wrangler deploy`)。
2. アップロードトークンを Muse のシークレット管理に登録し、
   カスタムコネクタ経由で publish.py / session_watcher.py から使える
   ようにする (コードにトークンは書かない)。
3. `gas/` を設置する (手順は gas/README.md)。
4. iOS アプリに Worker の URL と GAS のキック先を設定する。
5. `config.sample.json` を `coach_config.json` にコピーして自分の
   環境に合わせ、`comment_stock.sample.json` を `comment_stock.json`
   にコピーしてコメントを差し替える。
6. [フック単発実行手順](HOOKS.md)に従い、20秒間隔で `hook_once.sh --config ...` を呼ぶ。常駐は不要。

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

単発モードは `coach_state.json` に送信ルールの進行状態と実行途中の送信を保存します。
監視サイクルは各回で終了し、認証はフック実行時の環境から受け取ります。

## 高度を使ったコーチング

`config.sample.json` を実運用の `coach_config.json` に反映するときは、次の `altitude` 設定を使います。項目を省略した場合も同じ既定値です。

| 設定 | 既定値 | 用途 |
|---|---|---|
| `altitude.enabled` | `true` | 高度をコメント判定・終了文面に使う。`false` でもポーラーの高度収集は継続 |
| `altitude.downhill_net_loss_m` | `20` | 直近の完了kmで「下降量−上昇量」がこの値以上なら、速すぎ指摘を抑制 |
| `altitude.climb_gain_m` | `20` | 直近kmの上昇量がこの値以上なら、上りコメントの候補にする |
| `altitude.finish_ascent_m` | `50` | 累積上昇量がこの値以上なら、終了文面に獲得標高を添える |

単位はメートルです。下りによる抑制はkm境界から150m以内に限定し、古い下りの情報を次の区間へ引きずらないようにしています。瞬間的な勾配判定ではないため、区間途中の坂の変化を完全には反映しません。上りコメントは、そのkmに対応する間だけ、重要な注意・節目・通常素材の後、汎用コメントの前に選びます。通常素材がある間は上りコメントは出ません。メッセージの背骨と心拍バンドは変えません。

ポーラーは `altitude` を読み、スプリットに `elev_gain_m` / `elev_loss_m`、statusに `current_altitude_m` / `total_ascent_m` を追加します。最新の完了スプリットは `last_elevation_split` としても返し、kmをまたがない取得でも参照できます。

`ALTITUDE_DEADBAND_M = 2.0` は最後に採用した高度との差が2m未満なら累積しない方式です。比較基準を毎ポイント更新しないため、1mずつの緩い上りも累積できます。採用した高度差を現在のポイント間の距離比でkmへ配分するため、境界付近には配分の近似誤差があります。2m以上のノイズや長期ドリフトは除去できず、獲得標高は推定値として「約」を付けて伝えます。

高度が欠けた点や非数値・非有限値は飛ばし、その前後の高度差を推測して加算しません。最終点の高度が欠けると `current_altitude_m` は `null` です。必要な基準値・累積値は `poll_state.json`、監視側の直近スプリット・累積上昇量は `coach_state.json` に保存し、単発実行をまたいで引き継ぎます。旧stateは既定値で読めますが、アップデート以前や欠損区間の高度は復元しないため、そのランの総上昇量が少なくなる場合があります。切替はラン間に行ってください。

合成ポイントによるPythonテストで累積・再開・判定を確認しています。実際のLiveTrack高度の精度、坂でのコメントと終了音声の聴取は、本番配置後にMuse側で検証してください。
