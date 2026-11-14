# Connect IQ ライブ取り込み

`POST /upload?op=live` は既存 `UPLOAD_TOKEN` を `X-Upload-Token` で受け、必須キーを確認して `storedAt`（Worker の UTC 時刻）を上書きした JSON を `live-session.json` に保存します。`GET /live-session.json` は `X-Read-Token` または `X-Upload-Token` が必要で、未保存なら 404 です。既存の queue、session、音声、GAS 経路には変更を加えていません。

ローカル検証: `node --test worker/tests/live.test.mjs`。

デプロイは利用者が実施します。稼働時間外に `worker/` で既存の Cloudflare アカウント・R2 binding・secret を確認し、`npx wrangler deploy` を実行してください。secret の再作成や queue へのテスト書き込みは不要です。デプロイ後は、匿名 POST 401、認証 POST 200、匿名 GET 401、読み取りトークン付き GET 200 と `storedAt`、不正 JSON 400 と既存値の保持、認証付き queue GET 200 を確認してください。テスト POST は live-session.json を上書きするので、実運用のセッションがないときに限ります。テストデータは残ります。
