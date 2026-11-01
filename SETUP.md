# Voice Coach for Muse（VCM）セットアップ手順

他の人が同じ仕組みを構築するための手順書です。各自のエージェント実行環境と TTS を用意し、上から順に設定・検証することで、Garmin LiveTrack を Muse が監視し、走行中に音声コーチングがイヤホンへ流れる状態まで到達できます。

## 全体像

```
Garmin 時計 ──LiveTrack──▶ 開始通知メール(Gmail) ──▶ Muse が検知
Muse ──ポーリング──▶ livetrack.garmin.com（走行データ取得）
Muse ──コーチング文 + TTS──▶ MP3 ──HTTPS アップロード──▶ サーバー(queue.json + MP3)
iOS アプリ ──約5秒間隔で queue.json を取得──▶ 新着を再生（音楽をダッキング）
```

## ステップ 0: 必要なもの

- LiveTrack 対応の Garmin 時計と Garmin Connect アカウント
- iPhone（受信アプリ用。無料の Personal Team 署名で実機インストールする場合は Mac + Xcode）
- `queue.json` と MP3 を HTTPS で公開でき、書き込み用エンドポイントを置ける Web サーバー
- Gmail の開始通知メールを読み取れる形で連携した Muse（または同等のエージェント基盤）。メール送信権限は本手順では不要
- Muse 側で Python 3 を実行でき、20〜30秒間隔の繰り返し実行とローカル状態の保持ができる環境
- 複数文の音声を結合する ffmpeg（libmp3lame 対応）
- TTS（テキスト読み上げ）の手段。`publisher/publish.py` は互換 CLI を環境変数で指定するか、各自のコピーの `tts_speak()` を適合させます。音声合成そのものは各自が用意します

作業はリポジトリのルートを起点にします。サーバーへのアップロード用トークンと LiveTrack の閲覧用トークンは別物です。実値・状態ファイル・生成音声を Git に追加しないでください。

## ステップ 1: Garmin 側の設定

1. Garmin Connect アプリで、LiveTrack のメール受信者に「Muse が読めるメールアドレス（Gmail）」を追加します。
   - 受信者はスマホの連絡先に登録されている必要があります。iOS では Garmin Connect に連絡先へのアクセス許可が要る場合があります。
2. LiveTrack の自動開始（Auto Start）をオンにすると、アクティビティ開始と同時に通知メールが送られます（手動開始でもメールは送られます）。
3. テスト送信で、件名「Watch <名前> の LiveTrack」のような開始通知メールが届くことを確認します。メール本文の URL が `https://livetrack.garmin.com/session/<SESSION_ID>/token/<TOKEN>` の形です。

## ステップ 2: サーバー（配信キュー）の設置

詳細は [server/README.md](server/README.md) を参照してください。要点は次のとおりです。

1. 公開領域に `livetrack/` と `audio/` を作り、`server/upload.php` を設置します（例: `https://<your-domain>/livetrack/upload.php`）。初回だけ `queue.json` を `{"session":"standby","messages":[]}` で作成します。
2. アップロード用トークンを生成し、サーバー側には自分で生成した SHA-256 ハッシュのみを保存します（同梱定数のまま使わない）。トークンそのものはサーバーに置きません。
3. `queue.json` には `Cache-Control: no-store` を設定し、ディレクトリ一覧は無効化します。
4. このサーバーの URL（例: `https://<your-domain>/livetrack/queue.json`）が、後のステップでアプリに設定するキュー URL になります。

**チェックポイント:** queue の認証なし GET が HTTP 200 で空の JSON を返し、トークンなしの upload.php への POST が 401 になること。

## ステップ 3: iOS アプリのビルドと設定

詳細は [iOS README](livetrack-receiver-ios/README.md) を参照してください。

1. Xcode で `livetrack-receiver-ios/Voice Coach for Muse.xcodeproj` を開き、自分の Personal Team で署名して iPhone にインストールします。
2. **注意: 無料プロビジョニングの有効期限は約 7 日間です。** 期限が切れるとアプリが起動できなくなるため、実走の前に再インストールしてください。常用する場合は Apple Developer Program（有料）への登録を検討してください。
3. アプリの設定で、キュー URL にステップ 2 の `queue.json` の URL を入力します。
4. 音楽アプリ（Spotify 等）を再生しながら、後のステップ 7 のテスト配信で、ダッキング（音楽の音量低下と復帰）とロック画面での受信を確認します。Web ブラウザ版ではダッキングが実現できないため、受信はこのネイティブアプリを使います。

**チェックポイント:** アプリの「音声テスト」で音楽の音量低下・復帰を確認します。「コーチング開始」で最終受信成功時刻が更新されることも確認してください。バックグラウンド受信を使う場合はアプリの設定と位置情報権限を設定します。前景専用スキームではロック中の受信は対象外です。

## ステップ 4: publisher の設定

詳細は [publisher/README.md](publisher/README.md) を参照してください。

1. **`LIVETRACK_UPLOAD_ENDPOINT` を `https://<your-domain>/livetrack/upload.php` に明示設定**します（原本の既定接続先は著者のサーバーです）。`LIVETRACK_QUEUE_DIR` には自分の状態保存先の絶対パスを設定します。アップロード用トークンを環境変数 `LIVETRACK_UPLOAD_TOKEN` に設定します（スクリプト内に直書きしない）。
2. `LIVETRACK_TTS_BIN` と `LIVETRACK_TTS_VOICE` を設定して互換 TTS CLI を使います。互換でない場合は各自のコピーの `tts_speak()` を適合させます。声は 1 つ選んで固定するのがおすすめです。
3. 音声作成の方針（文単位の分割生成、音量の正規化など）は、[トップ README](README.md) の「Muse へのコーチング指示 > 音声（MP3）の作成方針」に従います。
4. サーバーによってはデフォルトの User-Agent が WAF に弾かれることがあります。その場合はアプリ相当の User-Agent を付けてください。

**チェックポイント:** `python3 publisher/publish.py init setup-test` → `python3 publisher/publish.py sync` の順で実行し、公開 queue が新しい空セッションになることを確認します。これは配信内容を置き換える操作なので、運用中のセッションでは行いません。出力の `ok: true` を確認してください。なお原本は文単位の分割・結合を実装していますが、README のラウドネス正規化処理は未実装です。必要な音量調整は各自の TTS 出力側で行います。

## ステップ 5: ポーリングスクリプトの配置

1. `monitor/livetrack_poll.py` を、Muse が実行できる場所に置きます（Python 3、標準ライブラリのみ）。
2. 開始通知メールから取得した値を `SESSION_ID`、`LIVETRACK_TOKEN` として実行環境へ安全に渡します。使い方（1回呼び出すと1回取得して終了）:

   ```
   python3 monitor/livetrack_poll.py --session-id "$SESSION_ID" --token "$LIVETRACK_TOKEN" --state monitor/state/run-example.json
   ```

   - `--state` にはランごとの状態ファイルを指定し、同じランの間は同じファイルを使い続けます（増分カーソルと km スプリット計算を保持します）。
   - 正常時の出力は JSON 1 件で、`ok`、`session.ended`、`status`（距離・時間・現在心拍・現在ペース）、`new_splits` が含まれます。取得できない項目は省略されます。失敗時は `error` と `session` を確認します。
   - セッション終了後はトラックポイント取得が 403（1008 SESSION_ACCESS_DENIED）になりますが、`session.ended` が true なら「失敗」ではなく「終了」です。
   - LiveTrack のトークンは「リンクを知る人が現在位置を見られる」資格情報です。公開 queue・ログ・Git に書き出さず、資格情報ストア等で管理してください。このスクリプトはトークンを状態ファイルに保存しません。引数に渡すため、実行環境のプロセス一覧から見える可能性にも留意します。

**チェックポイント:** 同じ状態ファイルで繰り返すとカーソルが引き継がれることを確認します。非公式 API を利用するため将来の互換性は保証されません。詳細は [monitor/README.md](monitor/README.md)。

### 監視ループも配置する場合

`monitor/coach_monitor.py` と2つの `*.sample.json` も収録しています。[監視ループの準備・実行方法](monitor/README.md)に従って、サンプルを非公開の `state/` にコピーし、特に `queue_json` を publisher の実保存先へ、`stock_file` をコピー先へ合わせます。原本サンプルのパスのまま起動しないでください。

Muse がセッション情報とネタ帳を準備した後、ルートから `python3 monitor/coach_monitor.py --config state/coach_config.json --session state/session.json --neta state/neta.json` で開始します。publisher のトークン・TTS 用環境変数も継承させます。ランごとの一意な配信セッションの init / sync は起動前に行います。

**原本の制約:** 終了403では距離が欠損し「0キロ」となる可能性があります。終了処理は再生確認ではなく固定待機後に standby と cleanup 0 を実行し、終了音声の送信失敗でも進みます。手動の再生確認手順と同等ではありません。無人運用前に [制約一覧](monitor/README.md) を確認し、テスト環境で終了経路も検証してください。今回は原本を変更していません。

## ステップ 6: Muse 側の設定

1. Muse に Gmail を連携し、ステップ 1 で設定した受信メールボックスを読めるようにします。
2. トップ README の「Muse へのコーチング指示」を、スキルやカスタム指示として Muse に登録します。`{{ }}` の項目（名前、目標レース、ペース帯、心拍ゾーン、ホームコースなど）は自分の情報に置き換えます。
3. 開始検知の方法を Muse と取り決めます。開始通知メールからセッション情報を抽出する正規表現は次のとおりです（短縮リンクの場合はリダイレクト先を解決してから抽出）。

   ```
   livetrack\.garmin\.com/session/([0-9a-fA-F-]{36})/token/([0-9a-fA-F]+)
   ```

4. Muse 側に、依頼されたランだけを対象として20〜30秒間隔でポーラーを呼び、JSON を読んで本文を生成し `publish.py say` で配信する実行手順を設定します。`coach_monitor.py` を使う場合は定期実行とメッセージの骨格生成をプログラムが担い、Muse は設定とネタ帳を供給します。スクリプトを置くだけではメール監視やプロセス起動は自動化されません。実行許可、トークンの注入、状態保存先をエージェント環境で設定します。

**チェックポイント:** 通知メールからセッション情報を抽出でき、Muse がポーラーと publisher を実行できること。依頼なしで監視を始めないこと。

## ステップ 7: 動作確認

1. **テスト配信**: ランをしなくても、`python3 publisher/publish.py say setup-test-001 '配信の確認です。'` のように毎回異なる ID で時刻メッセージ等を 30 秒間隔で 10 件ほど送り、アプリで全件再生されること、遅延（著者環境では体感10〜15秒。保証値ではない）、毎回のダッキングと音量復帰、ロック画面での受信を確認します。
2. **短い実走**: 実際に LiveTrack を開始し、開始通知メールからの自動検知、ポーリング、1km ごとのコーチング、終了メッセージまでを通しで確認します。
3. 終了音声を聞き終えたことを確認してから `python3 publisher/publish.py init standby` → `python3 publisher/publish.py sync` → `python3 publisher/publish.py cleanup` の順で実行します。既定では1時間以上古い未参照音声が対象です。即時削除の `cleanup 0` はすべての再生と発行が終わった場合のみ使います。アプリは ACK を送らないため、アップロード成功だけでは再生完了の確認になりません。

## 運用の流れ（1 回のラン）

1. ランナーが Muse に「ロングランするので監視して」と依頼する（監視はオンデマンドのみ）。
2. ランナーが LiveTrack を開始 → Muse が開始通知メールを検知し、ポーリング開始。
3. 走行中、Muse がコーチングを生成して配信（形式と内容は「Muse へのコーチング指示」に従う）。
4. セッション終了を検知 → 終了メッセージを配信し、確認できてからキューを待機状態へ戻し、MP3 を掃除する。
5. Muse が確定値（完了したアクティビティ）で総括を伝える。

## トラブルシューティング

| 症状 | 確認すること |
|---|---|
| 開始通知メールが届かない | Garmin Connect の受信者設定、連絡先への登録、迷惑メール振り分け |
| ポーリングが 403 になる | セッションが終了していないか（終了後は仕様）。CSRF トークン取得を含む bootstrap が通っているか |
| アプリに新着が来ない | キュー URL の設定、`queue.json` の no-store、アップロード順序（MP3 → queue.json の順） |
| 音楽がダッキングしない | Web 版ではなくネイティブアプリで再生しているか。AVAudioSession の設定 |
| 音声が小さい／音楽に負ける | TTS 側の音量正規化（README の音声作成方針）。ダッキング量は OS 側で固定 |
| 文と文の間に長い無音がある | TTS を文単位で分割生成し、短い間隔で連結する |
| アプリが起動できない | 無料プロビジョニングの期限切れ。再インストールする |
