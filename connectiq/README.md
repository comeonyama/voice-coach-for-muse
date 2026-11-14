# Connect IQ 連携 PoC（段階 0〜2）

標準ランのデータフィールドから約5秒ごとに数値を送り、専用 iOS アプリで受信・記録します。既存の VCM アプリや Muse には接続しません。対象は fēnix 8 AMOLED 43mm (`fenix843mm`) と 47/51mm (`fenix847mm`) です。Solar などは対象外です。

**状態:** iOS アプリは Xcode 27.2 で署名なしの実機向けビルドが成功しています。ウォッチ用は Connect IQ SDK 9.3.0 で表示専用版・通信版とも 43mm と 47/51mm 向けのビルドが成功しています（ビルド検証には一時的な開発者キーを使用）。Data Field での `transmit()` の実行、接続の安定性、バックグラウンド動作、電池消費は実機検証前には保証できません。特に Garmin の資料は Data Field に Communications の一部が使えるとしていますが、個別 API の対応は PoC で確認します。

## 段階 0: 開発環境

1. 利用者本人が [Garmin Connect IQ SDK Manager](https://developer.garmin.com/connect-iq/sdk/) をダウンロードし、Garmin アカウントでログインします。SDK Manager で Connect IQ SDK と、上記の実機に対応するデバイス定義をインストールします。Java 11 以降と、必要なら VS Code の Garmin 提供 Monkey C 拡張を用意します。[Garmin の導入手順](https://developer.garmin.com/connect-iq/connect-iq-basics/getting-started/)も参照してください。
2. 署名用の RSA-4096 開発者キーがなければ、VS Code のコマンドパレットで `Monkey C: Generate a Developer Key` を実行し、リポジトリの外に保存します。既存のキーがある場合はそれを利用できます。キーは公開・コミットしないでください。
3. このディレクトリの `PocReceiver/PocReceiver.xcodeproj` を Xcode で開き、Garmin の [Companion App SDK](https://github.com/garmin/connectiq-companion-app-sdk-ios) Swift Package の取得を待ちます。Signing & Capabilities で自分の Team と一意な Bundle Identifier を設定し、iPhone にビルド・インストールします。iOS 16 以降が対象です。署名なしのコンパイル確認は次のとおりです。

   ```sh
   xcodebuild -project PocReceiver/PocReceiver.xcodeproj -scheme PocReceiver \
     -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build
   ```

   Xcode が選択されていない場合は `DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer` をコマンドの先頭に付けます。署名なしビルドは実機へのインストールを意味しません。

## ウォッチ用ビルドとサイドロード

SDK Manager でインストールした SDK の `bin/monkeyc` を使用します。`CIQ_SDK` は実際の SDK ディレクトリ、`CIQ_KEY` は開発者キー、`CIQ_DEVICE` は実機に合わせて設定します。以下はこの README のあるディレクトリから実行します。出力をリポジトリ外に置く例です。

```sh
CIQ_SDK="$HOME/Library/Application Support/Garmin/ConnectIQ/Sdks/<インストールしたSDKディレクトリ>"
CIQ_KEY='<開発者キーの絶対パス>'
CIQ_DEVICE=fenix847mm # 43mm は fenix843mm
mkdir -p /tmp/vcm-connectiq-poc
cd VcmDataField
# 段階1: 通信しない表示専用版
"$CIQ_SDK/bin/monkeyc" -f stage1.jungle -d "$CIQ_DEVICE" -y "$CIQ_KEY" -o /tmp/vcm-connectiq-poc/VCM.PRG
# 段階2: 通信版（段階1完了後に上書きして作る）
"$CIQ_SDK/bin/monkeyc" -f monkey.jungle -d "$CIQ_DEVICE" -y "$CIQ_KEY" -o /tmp/vcm-connectiq-poc/VCM.PRG
```

それぞれのビルド後、ウォッチを USB 接続して `VCM.PRG` を `/GARMIN/APPS` にコピーします。fēnix 8 は MTP で接続するため、macOS では Finder や `diskutil` に通常のドライブとして現れません。Mac からファイルを転送するには [OpenMTP](https://openmtp.ganeshrvel.com/) などの MTP 対応ソフトを使う方法があります。Garmin 製ではないソフトのため、利用は各自の判断で行ってください。Garmin Express の同期完了後にメニューバーからも完全に終了し、OpenMTP を起動して Garmin → APPS にコピーします。接続先を使えるアプリは一度に一つです。ファイル転送の完了を確認してから USB を外します。Windows のエクスプローラーでも MTP 経由でコピーできます。段階2では段階1の PRG を置き換えます。VS Code の `Monkey C: Build for Device` は標準の `monkey.jungle`（通信版）を使えます。[Garmin のサイドロード手順](https://developer.garmin.com/connect-iq/connect-iq-basics/your-first-app/)も参照してください。

ウォッチの標準ランのデータ画面に Connect IQ フィールド `VCM PoC` を追加します。文字が切れる場合は広い表示枠を使います。検証中はこのフィールドが表示される画面を維持します。表示中以外も同様に動くことは別途検証が必要です。

## 段階 1: 表示のみ

表示専用版を導入し、タイマーを開始して距離・心拍・ペースを標準フィールドと見比べます。ペースは累積距離差とタイマー時間差から算出する約30秒の移動平均で、Garmin 標準の瞬間ペースとは一致しません。開始後5秒未満・停止時・距離未取得時は `--:--` です。画面下の `DISPLAY ONLY` を確認します。手動ラップおよび停止・再開も試します。段階1では送信しません。

## 段階 2: 受信と継続性

1. 通信版をビルド・サイドロードします。iPhone で Garmin Connect にログインし、ウォッチをペアリング、Bluetooth を有効にします。PoC アプリを起動し Bluetooth を許可して「Garmin デバイスを選択」から Garmin Connect に移り、対象ウォッチを1台だけ選びます。戻ったら通信準備完了を確認します。選択したデバイスは次回起動時にも復元します。
2. 標準ランを開始し、PoC アプリの最新値、ウォッチの `OK` / `ERR` と seq を確認します。`OK` はウォッチ側の通信完了通知であり、iOS のログ記録を保証するものではありません。タイマー停止中は送信しません。同期後、ランニング中に約30分、その後別のセッションで2時間測定します。開始・終了時刻、手動停止・オートポーズ、画面が表示されていたか、iPhone の画面状態とアプリの状態を記録します。まず iPhone アプリを前面にして検証し、必要に応じてロック・バックグラウンドも別条件として測定します。
3. アプリ内の「ログを共有」、または iPhone の「ファイル」→「この iPhone 内」→「VCM IQ PoC」から JSONL を取り出します。アプリ起動ごとに別ファイルです。記録には到着 UTC 時刻、seq、timerTime(ms)、累積距離(m)、心拍、時計が観測したタイマー状態と直近の停止・再開情報が入ります。未取得値は `-1` です。ログには運動情報が含まれますので、公開前に内容を確認してください。
4. Mac に取り出したログを集計します。

   ```sh
   python3 analyze_log.py '<取り出したログのパス>'
   ```

   連続した増加区間ごとに、受信した最初から最後までの seq の欠番率を算出し、最大到着間隔と30秒超の間隔を表示します。seq が減少する箇所はリセット・再起動・順序入れ替わりの可能性として別途確認します。seq は**送信を試みる時だけ**増え、完了待ちで見送った5秒枠には割り当てません。そのため欠番率だけでは送信機会の欠落を評価できず、最初の受信前・最後の受信後の欠落も分かりません。開始・終了時刻と到着間隔も併せて評価します。停止中は意図的に送信しないため、30秒超の空白をタイマーの進みとテスト記録で分類します。タイマーの進みは判定の補助で、時計変更等も含め自動的に停止と断定しません。

**合否:** 2時間のセッションを通じて受信が継続し、欠番が散発的で、タイマー稼働中に30秒を超える空白が多発しないこと。開始・終了付近の未受信、通信完了待ちによる長時間停止も確認します。「散発的」「多発」の定量閾値はこの PoC では未設定のため、集計値と運転条件を併記して判断します。30分が成立してから2時間へ進みます。

## 設計メモ

- Data Field の `compute()` で渡される Activity.Info を使用します。タイマー稼働中は約5秒ごと、停止・再開イベント時にも送信を試みます。通信中のイベントは最新状態を保持して完了後に送ります。通常の送信にも直近の停止・再開情報を含めます。再送やイベントの履歴キューはなく、複数のイベントが続いた場合は上書きされます。同期例外も `ERR` として扱います。コールバックが返らない場合は次を送れません。
- `state` は送信時の Activity.Info.timerState（0=OFF、1=STOPPED、2=PAUSED、3=ON、-1=不明）、`stop` / `resume` は時計がイベントを観測した UTC の Unix 秒、`stopKind` は 1=停止、2=オートポーズ、0=未観測、`ev` はインスタンス内のイベント累計です。時刻は時計の設定や変更の影響を受けます。停止・再開通知の到着や時刻の精度は実機で確認が必要であり、アプリ再起動や複数の未送信イベントがある場合、全履歴は復元できません。`state` とイベント時刻は取得タイミングが異なり、一時的に一致しないことがあります。
- `onTimerLap()` は引数がないため、イベント時に累積距離と timerTime を取得します。初期ラップ番号は1です。開始前のゼロから追跡できない場合やイベント時に値が取れない場合はラップ値に誤差が出ることがあります。ラップ開始時の timerTime は保持しますが、今回の送信には含めません。
- 同梱の表示専用版と通信版は UUID `7ac29246-e724-4d45-a145-41d19c543680` を共有します。iOS は GCM で得たデバイスとこの UUID の IQApp を登録します。GCM はデバイス選択に使い、受信は SDK の BLE 接続です。
- バックグラウンド BLE と state restoration を有効にしていますが、iOS による停止や Bluetooth の状態に左右されます。アプリの強制終了後の継続受信は保証しません。実機で連続性を確認してください。
- フィールド値は取得できない場合があります。辞書は数値のみとし、欠損を `-1` で送ります。seq はプロセス存続中に単調増加し、タイマーリセットでは戻しません。アプリの再起動では戻ります。距離・心拍等は個人の運動情報です。
- ログ記録はアプリ起動単位です。iOS アプリは本番と別の Bundle ID を使い、VCM の本番データを変更しません。

公式資料: [Communications](https://developer.garmin.com/connect-iq/api-docs/Toybox/Communications.html)、[Activity.Info](https://developer.garmin.com/connect-iq/api-docs/Toybox/Activity/Info.html)、[DataField](https://developer.garmin.com/connect-iq/api-docs/Toybox/WatchUi/DataField.html)、[Garmin iOS サンプル](https://github.com/garmin/connectiq-companion-app-example-ios)。

## ライブ取り込み・定型読み上げ（開発中）

PoC の「Worker・読み上げ設定」で HTTPS Worker URL とアップロードトークンを設定します。トークンは Keychain に保存され、画面では入力をマスクします。未設定なら送信しません。保存後、読み上げはラップごと・3分・1分・オフを選べます。設定値は本番コーチングとは独立しています。「受信を終了」で明示的に終了通知を送信し、「新しい受信を開始」で次の受信を受け付けます。

最新データを15秒ごと、および初回・タイマー状態変更・受信終了時に送ります。送信中の要求は最新状態にまとめ、失敗した内容は蓄積しません。送信成功／試行数と、アプリ起動中の最大受信間隔を画面に表示します。通信断は終了扱いにしません。UUID はアプリ再起動をまたいで保持し、タイマーの巻き戻りを新しい活動の手掛かりとします。時計の再起動などで同一性を完全には判定できず、利用者による受信の終了・新規開始操作が必要になる場合があります。

直近ペースは受信したタイマー時間と距離の直近30秒の差分（30秒未満は10秒以上）で計算し、停止・再開で窓を破棄します。全体ペースはタイマー時間÷距離。心拍は PoC の固定バンド（120以下＝余裕あり、135以下＝適正、150以下＝少し高め、それ超＝高め）で判定します。現行ペイロードでは完了ラップの正確な値を取得できず、推測しません。GPS は送信しません。

TTS は発話音量を最大にし、発話中だけ他の音声を duck して終了後に解除します。端末の出力音量は利用者が調整します。PoC にコーチング再生機能はないため、コーチングの優先制御は本番移植時の課題です。iOS のバックグラウンド制約により、15秒周期の実行・送信や読み上げの継続は保証されません。実機で停止・再開、屋外音量、R2 との照合が必要です。
