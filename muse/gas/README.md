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
3. 左メニュー「プロジェクトの設定」→「スクリプト プロパティ」で以下の必須プロパティを追加:
   - `KICK_SECRET`: キック用の合言葉。自分でランダムな文字列を決める
     (アプリ側の設定にも同じ値を入れる)
   - `UPLOAD_TOKEN`: Worker の secret (UPLOAD_TOKEN) と同じ値
   - `WORKER_BASE`: 自分の Worker の URL を設定すること（必須）。
     未設定・空白の場合は `WORKER_BASE is not set` エラーとなり、
     キックへの応答は `ok:false` と理由を返す。実行ログにも理由を記録する。
4. エディタで関数 `debugCheck` を一度実行し、Gmail と外部接続の
   権限承認を済ませる (開始メールが無ければ {ok:true, found:false} が
   ログに出れば正常)。
5. 「デプロイ」→「新しいデプロイ」→ 種類「ウェブアプリ」:
   - 次のユーザーとして実行: **自分**
   - アクセスできるユーザー: **全員**
   デプロイ後に表示される **/exec の URL** を控える (アプリ設定に入れる)。
   コードを直したら「デプロイを管理」から既存デプロイを編集して
   バージョン更新すると URL が変わらない。
   処理済みメールの `found:false` 応答への修正も再デプロイが必要で、既存デプロイを編集して「新しいバージョン」にすれば /exec URL は変わらず、アプリの URL 更新は不要。
   **新しいデプロイで /exec URL が変わった場合は、iOS アプリの
   「検知スクリプト URL」も必ず新しい URL に更新すること。**
6. アプリ側の設定に、/exec の URL と KICK_SECRET を登録する。

## アプリ開始を基準にする契約

通常操作は「アプリでコーチング開始 → 時計でアクティビティ開始」。
キックは `key` に加え、UUIDの `sid` とISO 8601 UTCの `since` が必須。
欠落・不正な要求は `ok:false` とし、旧方式には戻さない。
メールは `since - 120秒` 以降かつ受信から30分以内の未処理候補に限定する。
120秒は時計ずれの許容であり、この範囲内の開始前メールは候補になり得る。
同じsidの投稿成功後は `found:true, already:true` を返して再投稿しない。
`LAST_START_ID` / `LAST_START_SESSION_ID` は自動管理する対応情報。
Workerには `start_id` を追加して渡す。

アプリは再起動後も同じ待機を再開する。GASのfoundはメール検知だけを示す。
監視中表示は公開 `start-status.json` の自分のstart_idがmonitoringの場合だけ。
当日のqueue名だけではキックを停止しない。

`scheduledCheck` の時間主導トリガーは停止すること。開始IDがない定期検知は
今回の方式では行わず、この関数を呼ぶとエラーになる。
`debugCheck` はテスト用UUIDと現在時刻を使う。実際に投稿するため実走中は実行しない。

## デプロイ順序

準備としてWorkerにstart-statusルートを反映し、VMのウォッチャーも更新する。
これらはstart_idのない手動セッションにも対応する。

1. 新アプリを先に配布する（旧GASは追加パラメータを無視する）。
2. GASは既存デプロイを編集して「新しいバージョン」に差し替える。
   /exec URLは変更しない。新しいデプロイは作らない。
3. GAS更新後は旧アプリでは検知できなくなるのが仕様。

移行途中の旧GASでは新しい時刻ゲートは有効にならない。全コンポーネントを
反映してから、新しい開始操作で通し確認する。

## 自動管理するプロパティ

`PROCESSED_MAIL_IDS` は処理済みメッセージIDのJSON配列で、古い登録から落として
直近20件を保持する。手動設定は不要。既存の `LAST_MAIL_ID` はリストに取り込み、
`LAST_MAIL_ID` / `LAST_SESSION_ID` も投稿成功後に更新する。
リストが不正なJSON・型の場合はエラーとし、勝手に消去して再投稿しない。

## 動作

- キックされると直近2時間の「livetrack」メールを新しい順に確認し、
  未処理の開始メールがあれば HTML から session_id / token を抽出
  (gar.mn 短縮リンクはリダイレクト先を解決) して Worker へ POST する。
- 検索結果を全ページ走査し、処理済みIDを除いた最新の開始メールを候補にする。
- 30分より古い候補は投稿せず `found:false` を返す。
- Workerへの投稿成功時だけIDを処理済みに追加する。投稿失敗時は
  `ok:false, found:false` とし、次回も再試行できるようにする。
- 処理済みメールだけの場合も `found:false` を返してキックを継続する。
- 検索反映待ちの新着は検知できない。30分以内の古い未処理メールが残っていれば
  それが候補になるため、この方式だけでは検索反映遅延による誤選択を完全には防げない。
- 応答は JSON: 検知時 {ok:true, found:true, session_id},
  未検知 {ok:true, found:false}。トークンは応答に含めない。
