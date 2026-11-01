# 発行側スクリプト（原本の受領待ち）

Muse 側の `custom.livetrack-upload/bin/publish.py` はこの Mac の作業フォルダ・Documents・Downloads では見つかっていません。実物を取得するまでは、別実装を原本として格納せず、`publish.py` は未収録としています。

原本受領後、トークン取得箇所だけを環境変数（例: `LIVETRACK_UPLOAD_TOKEN`）またはリポジトリ外の設定ファイルから読む形に一般化します。Muse 固有の資格情報ストアや connector.yaml への依存は持ち込みません。具体的な引数と使用例は原本確認後に記載します。

収録対象の操作は `init` / `say` / `sync` / `cleanup` です。実際の CLI、状態ファイルの形式、TTS との接続方法は未確認です。これらの操作を本番で推測実行しないでください。

セッション状態、生成音声、実運用 queue、ログ、資格情報はこのディレクトリの Git 管理対象に含めません。
