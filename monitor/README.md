# Voice Coach for Muse — LiveTrack ポーリング

`livetrack_poll.py` は提供された一般化版をバイト単位で変更せず収録しています。Python 3 標準ライブラリのみで動く、1回取得して JSON 1件を出力するスクリプトです。メール監視・定期実行・文章生成・音声配信は行いません。全体の手順は [SETUP.md](../SETUP.md) を参照してください。

```sh
python3 monitor/livetrack_poll.py --session-id "$SESSION_ID" --token "$LIVETRACK_TOKEN" --state monitor/state/run-example.json
```

- `--session-id` / `--token`: Garmin の開始通知メールにある LiveTrack 閲覧リンクから取得した値。アップロード認証トークンとは別です。
- `--state`: ランごとの状態 JSON。同じランでは同じパスを使い、次のランは新しいファイルにします。存在しなければ作成し、カーソル、距離・経過時間、km スプリット用の累積値等を保存します。トークンは保存しません。同じファイルを複数プロセスで同時更新しないでください。
- Muse 側で20〜30秒間隔の繰り返し呼び出しを設定します。

## 出力

| 項目 | 内容 |
|---|---|
| `ok` | 正常取得の成否。終了コードだけで判定しない |
| `error` | 失敗時の説明 |
| `session` | `start`、`end`、`ended` |
| `status` | 正常時の距離 `distance_km`、経過秒 `duration_secs`、最新点の古さ `last_point_age_secs`、現在心拍 `current_hr`、ペース `current_pace` / `current_pace_secs_per_km`、累積取得点数 `points_total` |
| `new_splits` | 新たに通過した km の `km`、`split_secs`、`pace`、`avg_hr` |

取得できない status 項目は省略されます。新しい点がない場合に現在心拍やペースが省略されることもあります。欠損値をゼロや推測値に置き換えないでください。

終了セッションではトラックポイントが HTTP 403（`1008 SESSION_ACCESS_DENIED`）を返し、出力が `ok: false` でも `session.ended: true` の場合があります。この組合せを終了として扱います。403 だけで終了と断定せず、セッション情報を確認してください。スクリプトの ended 判定は取得日時の差を用いた推定です。

## 状態と資格情報

LiveTrack のリンクは現在位置を閲覧できる資格情報です。トークン・セッション情報・状態ファイル・生の出力を公開 queue や Git に入れないでください。状態は `monitor/state/` 等の非公開領域に保存します。スクリプト引数はプロセス一覧に現れる可能性があるため、アクセスを制限した実行環境を使ってください。

原本にはサービスへの実接続先 `livetrack.garmin.com` が含まれます。これは利用者固有のホスト名や秘密情報ではなく、API 接続に必要な固定値です。原本指定に従い変更していません。非公式 API のためサービス側の変更で動かなくなる可能性があります。

構文検証のみを実施しています。今回の統合作業では実セッションへのアクセスや実走検証は行っていません。

## 監視ループ（coach_monitor.py）

提供原本を変更せず収録しています。プログラムがポーリング間隔、配信タイミング、距離の日本語読み・平均ペース・心拍判定、使用済みコメントの保存、終了処理を担います。Muse はメールからのセッション情報、各ランの設定とネタ帳（JSON の文字列配列）を供給します。メール監視自体は引き続き外部の役割です。

リポジトリルートから準備します。以下の `state/` は Git 管理対象外です。

```sh
mkdir -p state/coach
cp monitor/coach_config.sample.json state/coach_config.json
cp monitor/comment_stock.sample.json state/comment_stock.json
```

実行前に **コピーした設定** を編集します（サンプル原本は変更不要）。

- `queue_json`: publisher が保存する実際の `queue.json` の絶対パスに変更。`LIVETRACK_QUEUE_DIR` 配下の queue.json と必ず一致させます。サンプルの `state/queue_mirror.json` はそのままでは一致しません。
- `stock_file`: `state/comment_stock.json` または絶対パスに変更。
- `poll_script` / `publish_script`: ルートからならサンプルのまま。別の作業ディレクトリでは絶対パスに変更。
- `state_dir`: `state/coach` 等。`used_comments.json` を残すことでランをまたいで在庫コメントの再利用を避けます。`poll_state.json` はセッション ID が変わればリセットされます。
- `hr_bands` / `target_pace`: 自分の目標・心拍ゾーンへ調整。サンプルは個人への推奨値ではありません。
- `send_mode`: `per_km`（0.3km以降の初回と整数km通過時）、またはテスト向けの `interval`。後者の間隔は `send_interval_sec`。
- `poll_interval_sec`、`stale_finish_sec`、`max_duration_min`、`max_messages`、`close.wait_before_standby_sec`: 実行・停止条件を確認。

`state/session.json` は Muse が `{"session_id":"…","token":"…"}` の形式で非公開に作成します。実値を README やログ・Git に転記しません。任意の `state/neta.json` は事前に用意したコメントの文字列配列です。設定した publisher の環境変数・TTS は監視プロセスにも引き継がせます。

```sh
# ランごとに一意のセッション名で配信キューを初期化（運用中には実行しない）
python3 publisher/publish.py init run-example
python3 publisher/publish.py sync
python3 monitor/coach_monitor.py --config state/coach_config.json --session state/session.json --neta state/neta.json --stock state/comment_stock.json
```

ネタ帳を使わない場合は `--neta` を省略します。同じキューに複数の監視・発行プロセスを立ち上げないでください。メッセージ ID は `run-001` 等で、開始時の queue 件数から連番を再開します。

### 出力・停止と原本の制約

- 状態ディレクトリに `coach_log.jsonl`、`used_comments.json`、`poll_state.json` を保存します。標準出力に定期 JSON を出す設計ではありません。ログには走行情報が含まれます。
- 通常終了は終了コード0、連続する取得失敗で中断すると2。例外の経路では連続失敗カウントが増えず、上限時間まで継続する場合があります。
- `state_dir/coach_stop` を作ると停止します。次回はこのファイルを除去します。停止フラグ・時間上限・配信数上限・取得失敗中断では、終了音声や standby/cleanup は実行されません。
- 終了判定・データ停滞時は、終了音声 → 固定待機（サンプル60秒）→ init standby → sync → cleanup 0。**音声の送信失敗時にも後続処理が進み、再生 ACK も確認しません。固定待機は再生完了の保証ではありません。** 未再生の音声を消す可能性があります。
- ポーラーが終了403を返すと status は含まれないため、この版は終了文で距離0・平均ペース不明を使う可能性があります。終了値を確定値として扱わないでください。
- 重要助言や節目は通常の配信タイミングで選ばれ、独立した割り込み配信ではありません。在庫・ネタ帳は使用済み集合で除外されますが、節目文や生成フォールバックの完全な重複防止は保証されません。

これらは原本保持のため修正していません。自動の終了処理を含めて無人運用する前に、上記の挙動を確認してください。今回の追加では構文・サンプル JSON の検証を行い、本番への配信は行っていません。
