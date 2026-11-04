# VCM LiveTrack Detector (GAS) 設置手順

Garmin の LiveTrack 開始メールを Gmail から検知し、セッション URL を
Cloudflare Worker (vcm-queue) に渡すための Google Apps Script。
iOS アプリからのキックで起動するのが本線。Muse は検知の鎖から外れる。

前提: Worker 側に `op=session` 対応版がデプロイ済みであること
(`worker/worker.js` をデプロイする)。

## 手順

1. https://script.google.com を開き、新しいプロジェクトを作成。
   名前は「VCM LiveTrack Detector」など。
2. `livetrack_detector.gs` の内容をすべて貼り付けて保存。
3. 左メニュー「プロジェクトの設定」→「スクリプト プロパティ」で追加:
   - `KICK_SECRET`: キック用の合言葉。自分でランダムな文字列を決める
     (アプリ側の設定にも同じ値を入れる)
   - `UPLOAD_TOKEN`: Worker の secret (UPLOAD_TOKEN) と同じ値
   - `WORKER_BASE`: 任意。省略時はコード内の既定値
     (`https://vcm-queue.example.workers.dev` — 自分の Worker の URL に変更すること)
4. エディタで関数 `findAndPost_` を一度実行し、Gmail と外部接続の
   権限承認を済ませる (開始メールが無ければ {ok:true, found:false} が
   ログに出れば正常)。
5. 「デプロイ」→「新しいデプロイ」→ 種類「ウェブアプリ」:
   - 次のユーザーとして実行: **自分**
   - アクセスできるユーザー: **全員**
   デプロイ後に表示される **/exec の URL** を控える (アプリ設定に入れる)。
   コードを直したら「デプロイを管理」から既存デプロイを編集して
   バージョン更新すると URL が変わらない。
6. アプリ側の設定に、/exec の URL と KICK_SECRET を登録する。

## 任意: フォールバックの定期チェック

トリガー (時計アイコン) で `scheduledCheck` を時間主導・5分間隔に
登録しておくと、アプリのキックが無くても検知できる。常時有効だと
実行クォータを消費するので、基本はキックのみでよい。

## 動作

- キックされると直近2時間の「livetrack」メールを新しい順に確認し、
  未処理の開始メールがあれば HTML から session_id / token を抽出
  (gar.mn 短縮リンクはリダイレクト先を解決) して Worker へ POST する。
- 30分より古い開始メールは無視し、重複判定は最新1件のみで行う
  (過去のランの誤検知防止)。
- 処理済みメール ID とセッション ID はスクリプトプロパティに記録し、
  二重送信しない。
- 応答は JSON: 検知時 {ok:true, found:true, session_id},
  未検知 {ok:true, found:false}。トークンは応答に含めない。
