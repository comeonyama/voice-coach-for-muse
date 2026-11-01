# Muse LiveTrack Receiver

Museが生成したコーチングのJSONとMP3を受信・再生するiOSアプリと静的Webアプリです。

## プロジェクト

- [iOS版](livetrack-receiver-ios/README.md)：SwiftUI / iOS 17以降 / AVAudioSessionのduckOthersで音楽との共存を検証。
- [Web版](livetrack-receiver/README.md)：Vanilla HTML・CSS・JavaScript。ビルド不要。

iOS版は `livetrack-receiver-ios/LiveTrackReceiver.xcodeproj` をXcodeで開きます。実機利用時には自分のPersonal Teamを選んで署名してください。個人のTeam ID・証明書・プロビジョニングプロファイルは含めていません。

## 検証状況

2026-11-01、ユーザーのiPhone実機テストで、ネイティブ版のテスト再生時にSpotifyの音量が下がることを確認しました。音声終了後の音量復帰と、ロック画面での新着受信は未確認です。

Web版は同時再生には成功しましたが、音量低下が実現せず、割り込みモードではSpotifyが停止して自動復帰しませんでした。

## データ配信

Museが更新する `queue.json` とMP3をHTTP(S)で配信し、アプリにURLを設定します。相対音声パスはqueue.jsonを基準に解決します。受信専用で、サーバーへのACKや削除はありません。

詳細なJSON形式、ビルド・インストール方法、検証手順は各READMEを参照してください。
