# 発行スクリプト

> **注記（2026-11-07）:** このディレクトリは旧 XServer 構成（upload.php）向けの発行スクリプトの記録です。現行の発行は [`muse/publish.py`](../muse/publish.py) が Cloudflare Worker に対して行います。セットアップは [SETUP.md](../SETUP.md) を参照してください。以下の内容は当時の版の記録として保持しています。


`publish.py` はユーザー提供の Muse 側原本（`custom.livetrack-upload/bin/publish.py` 相当）です。受領した版は既に環境変数から認証情報を取得する設計のため、コードを変更せず格納しました。Muse 固有の資格情報ストアや connector.yaml は不要です。

## 必要なもの

- Python 3（Python パッケージの追加依存なし）
- `say` 用の TTS コマンド。既定は Muse の `tts` CLI
- 複数文の `say` には `ffmpeg`（libmp3lame 対応）

TTS は次の CLI と互換である必要があります。一般の TTS サービスを使う場合は同じ引数を受け取るラッパーを用意するか、`tts_speak()` を環境に合わせます。TTS 自体と定期実行・文章生成の仕組みは外部で用意します。LiveTrack ポーラーは [当時の monitor/](https://github.com/comeonyama/voice-coach-for-muse/blob/6be0ce1/monitor/README.md) に収録しています。

```text
<bin> speak --text <text> --voice <voice> --language ja --output <path>
```

出力は MP3（結合処理は 24 kHz モノラルを想定）。複数文は句点等で分割し、最大4並列で生成して0.35秒の間隔を挟んで結合します。

## 環境変数

| 変数 | 内容・既定値 |
|---|---|
| `LIVETRACK_UPLOAD_ENDPOINT` | HTTPS の upload.php URL。自分の `https://<your-domain>/livetrack/upload.php` を明示設定（原本の既定は著者環境） |
| `LIVETRACK_UPLOAD_TOKEN` | 認証トークン。必須（ローカルだけの init は不要） |
| `LIVETRACK_QUEUE_DIR` | ローカル状態の保存先。既定はスクリプト隣の `queue/`。絶対パスを推奨 |
| `LIVETRACK_TTS_BIN` | TTS 実行ファイル。既定 `tts` |
| `LIVETRACK_TTS_VOICE` | 音声。既定 `avocado_v2:MAI_01` |

トークンは資格情報ストア等から実行環境へ注入してください。コマンドラインやファイルに実値を書かず、端末で手入力するなら次の例で非表示入力できます。

```sh
export LIVETRACK_UPLOAD_TOKEN="$(python3 -c 'import getpass; print(getpass.getpass("Upload token: "))')"
export LIVETRACK_QUEUE_DIR="$PWD/publisher/queue"
# 必要なら接続先と TTS を設定（HTTPS の信頼する接続先のみ）
export LIVETRACK_UPLOAD_ENDPOINT='https://<your-domain>/livetrack/upload.php'
```

## 操作

以下は実行例です。sync / say / cleanup は指定したサーバーを実際に更新します。発行者は1プロセスに限定してください。

```sh
# 1. ローカル queue を空にする（サーバーはまだ更新しない）
python3 publisher/publish.py init run-2026-11-01

# 2. 空の新セッションをサーバーへ反映する
python3 publisher/publish.py sync

# 3. 本文を音声にして送信し、queue に追加・反映する
python3 publisher/publish.py say run-2026-11-01-001 '5キロです。予定通りです。'

# 4. 現在のサーバー queue が参照していない、1時間以上古い音声を削除
python3 publisher/publish.py cleanup
# 明示的に秒数を指定する場合
python3 publisher/publish.py cleanup 3600

unset LIVETRACK_UPLOAD_TOKEN
```

`init` はローカル queue を上書きするため、実行前にセッション切り替えのタイミングを確認します。`sync` はローカル queue のみ送信し、音声の再送は行いません。`say` は TTS → MP3 送信 → queue 送信 → ローカル queue 保存の順です。成功時には `ok: true` の JSON を出力します。

`cleanup 0` は新しい音声も削除対象になるため、発行処理中には実行せず、セッション切り替え完了後等に限定してください。終了時も queue に残っている音声は保護されます。全音声を削除するには、必要な再生が終わってから空のセッションを init → sync して cleanup を呼びます。

## 原本を維持したことによる注意点

- ID は `^[a-z0-9][a-z0-9-]*$` を満たし、同じセッションで再利用しないでください。ローカルのパス構築に直接使うため、外部入力を無検証で渡さないでください。音声名の衝突を避けるためセッション名も含める運用を推奨します。
- MP3 はサーバーの上限 2 MiB 以下に収めます。
- HTTP リダイレクトは拒否します。接続先には HTTPS の正しい URL を設定します。
- 失敗時も終了コードが 0 になる経路があります。自動運用では終了コードだけでなく出力 JSON の `ok` を確認します。
- queue 送信失敗時に音声だけ残る場合があります。サーバーの状態を確認して再試行し、不要音声は cleanup で整理します。ネットワーク応答の消失時には実際には反映済みの可能性もあります。
- ローカル状態に対する排他制御はありません。複数端末・複数プロセスからの同時発行は避けます。
- `.parts` 等のローカル生成ファイルは残ります。`queue/` 全体は Git 管理対象外です。

## 確認結果

原本とのバイト一致、構文、init のローカル出力、通信と TTS を差し替えた say / sync / cleanup の処理順・データ形式を確認しました。本番サーバーへの更新と実 TTS の呼び出しは行っていません。
