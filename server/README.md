# HTTPS アップロード受信プログラム

> **注記（2026-11-07）:** 本番の配信基盤は 2026-11-03 に Cloudflare Worker + R2（[`worker/`](../worker/)）へ移行しました。この PHP 版は旧構成の記録であり、現行の正本ではありません。セットアップは [SETUP.md](../SETUP.md) を参照してください。


`upload.php` は discoverroutes.jp の本番に設置済みの cleanup 対応版の作業コピーです。2026-11-01 に本番ファイルの SHA-256 と一致することを確認しました。

- SHA-256: `f4a202eb8efc14018cb2b9f264b3086090b97fc44d6b33fb665eb7fe36bc4b21`
- 認証用定数には SHA-256 ハッシュのみを格納しています。平文トークンは含みません。
- 今回はバックアップとして格納しただけで、本番ファイルは変更していません。

## 新しいサーバーへの設置

PHP 8 以降が動作する HTTPS 公開領域で、次の構成を作ります。WordPress の設定ファイルは変更しません。

```text
livetrack/
  upload.php
  queue.json
  .htaccess
  audio/
```

1. `livetrack/` と `audio/` を作り、PHP の実行ユーザーが書き込める権限を設定します。ディレクトリは通常 755、ファイルは 644 を起点に、ホスティング環境に合わせます。
2. 新しいトークンを生成し、資格情報ストアへ保存します。例: `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`。出力された実値をソース・ログ・リポジトリに保存しないでください。
3. 以下でトークンを非表示入力し、ハッシュだけを得ます。`upload.php` の `UPLOAD_TOKEN_SHA256` 定数をそのハッシュで置き換えます。これは新規設置用の手順であり、既存本番のトークン変更は不要です。

```sh
python3 -c 'import getpass, hashlib; print(hashlib.sha256(getpass.getpass("Upload token: ").encode()).hexdigest())'
```

4. 初回のみ `queue.json` を `{"session":"standby","messages":[]}` で作成します。既存運用中の queue を上書きしないでください。
5. 以下を **livetrack 内だけ** の `.htaccess` に設定します。Apache 以外では同等の設定を行います。

```apache
Options -Indexes
<IfModule mod_headers.c>
    <Files "queue.json">
        Header set Cache-Control "no-store"
    </Files>
</IfModule>
```

6. queue の公開 GET が HTTP 200、ディレクトリ一覧が表示されないこと、トークンなしの POST が 401、GET upload.php が 405 となることを確認します。

## API

すべて POST、`X-Upload-Token` ヘッダ必須。トークン欠如・不一致は 401（GET は 405）。成功は JSON `{"ok":true}`、失敗は `{"ok":false,"error":"理由"}`。

| 操作 | URL クエリ | ボディ |
|---|---|---|
| 音声配置 | `op=audio&name=example-001.mp3` | MP3 バイナリ。最大 2 MiB |
| queue 更新 | `op=queue` | `session` 文字列・`messages` 配列を含む JSON |
| 不要音声削除 | `op=cleanup` | 省略、または `{"min_age_sec":3600}` |

音声名は `^[a-z0-9][a-z0-9-]*\.mp3$` のみ（255 バイト以下）。音声を先に送信し、すべて成功してから queue を送ります。queue と音声の書き込みは同一ディレクトリの一時ファイルから rename します。

cleanup は現在の queue から参照されておらず、指定秒数以上古い音声のみ削除し、`deleted` と `count` を返します。既定は 3600 秒。`0` は新しい音声も対象になるため、音声送信から queue 反映までの途中には呼ばないでください。別の発行プロセスとの同時実行も避けます。シンボリックリンクは削除対象外です。不正・読取不可の queue は 500 で削除を中止します。

## 再現範囲

この PHP は監視・文章生成・TTS を行いません。入力の MP3 と queue を保存する受け口のみです。queue のデータ契約は iOS / Web 版と共通です。実運用の queue、音声、ログ、認証情報はこのディレクトリに格納しません。
