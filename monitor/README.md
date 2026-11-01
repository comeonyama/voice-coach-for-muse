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
