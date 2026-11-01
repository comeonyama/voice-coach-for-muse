import SwiftUI

struct ContentView: View {
    @StateObject private var model = ReceiverModel()
    @Environment(\.scenePhase) private var phase
    @State private var showSettings = false
    private let accent = Color(red: 0.84, green: 0.97, blue: 0.67)

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    HStack(alignment: .firstTextBaseline) {
                        Text("muse").font(.system(size: 38, weight: .bold, design: .rounded))
                        Spacer()
                        Text(model.foregroundOnly ? "前景専用" : "NATIVE COACH").font(.caption).foregroundStyle(accent)
                    }
                    Text(model.latestText)
                        .font(.system(size: 30, weight: .semibold)).lineSpacing(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityLabel("コーチング: \(model.latestText)")
                    Button {
                        if model.running || model.busy { model.stop() } else { model.start() }
                    } label: {
                        Text(model.running || model.busy ? "停止" : "コーチング開始")
                            .font(.title3.bold()).frame(maxWidth: .infinity).padding(.vertical, 12)
                    }
                    .buttonStyle(.borderedProminent).tint(accent).foregroundStyle(.black)
                    if model.paused {
                        Button("音声を再開") { model.resume() }
                            .buttonStyle(.borderedProminent).tint(.orange)
                    }
                    VStack(alignment: .leading, spacing: 12) {
                        LabeledContent("最終受信成功", value: model.lastPoll?.formatted(date: .omitted, time: .standard) ?? "—")
                        LabeledContent("未再生", value: "\(model.pendingCount) 件")
                        LabeledContent("Audio Session", value: model.audioStatus)
                        LabeledContent("位置情報", value: model.locationStatus)
                    }.font(.subheadline).foregroundStyle(.secondary)
                    Toggle("開始中は自動ロックを防止", isOn: $model.preventLock).tint(accent)
                    HStack {
                        Button("テスト再生") { model.testPlay() }.disabled(model.busy || model.pendingCount > 0)
                        Spacer()
                        Button("再生済みをリセット") { model.resetPlayed() }.disabled(model.busy || model.pendingCount > 0)
                    }.buttonStyle(.bordered)
                    if !model.notice.isEmpty { Text(model.notice).font(.footnote).foregroundStyle(accent) }
                    Text("最初はSpotifyを再生し、イヤホンを接続して「テスト再生」を押してください。音量の低下・復帰は耳で確認します。")
                        .font(.footnote).foregroundStyle(.secondary)
                    Divider()
                    Text("メッセージ履歴").font(.headline)
                    if model.history.isEmpty { Text("受信したメッセージがここに残ります。").foregroundStyle(.secondary) }
                    LazyVStack(alignment: .leading, spacing: 18) {
                        ForEach(model.history) { entry in
                            VStack(alignment: .leading, spacing: 6) {
                                Text("\(entry.message.timestamp?.formatted(date: .omitted, time: .standard) ?? entry.message.ts) · \(entry.message.id)").font(.caption).foregroundStyle(.secondary)
                                Text(entry.message.text).font(.body)
                                Text(entry.status).font(.caption).foregroundStyle(accent)
                            }
                            Divider()
                        }
                    }
                    HStack { Text("イベントログ").font(.headline); Spacer(); Button("ログをコピー") { model.copyLogs() } }
                    ScrollView {
                        Text(model.logs.joined(separator: "\n"))
                            .font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading).padding(12)
                    }.frame(height: 260).background(.black.opacity(0.3)).clipShape(RoundedRectangle(cornerRadius: 12))
                    Text("位置情報は保存・送信しません。バックグラウンドでの受信頻度はiOSの制御を受けます。")
                        .font(.caption).foregroundStyle(.secondary)
                }.padding(24)
            }
            .background(Color(red: 0.06, green: 0.085, blue: 0.07))
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("設定", systemImage: "gearshape") { showSettings = true } } }
            .sheet(isPresented: $showSettings) { settings }
        }
        .preferredColorScheme(.dark)
        .onChange(of: phase) { _, new in model.sceneChanged(new) }
    }

    private var settings: some View {
        NavigationStack {
            Form {
                Section("受信設定") {
                    TextField("queue.json のURL", text: $model.queueURL, axis: .vertical)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Stepper("ポーリング間隔：\(Int(model.interval))秒", value: $model.interval, in: 1...300, step: 1)
                    Button("公開デモURLを設定") { model.queueURL = ReceiverModel.demoURL }
                    Text("初期値は公開デモです。本番ではMuseが更新するqueue.jsonのURLに変更してください。設定は自動保存します。")
                        .font(.caption)
                }.disabled(model.settingsLocked)
                Section("バックグラウンド検証") {
                    Toggle("開始中に位置情報更新を利用", isOn: $model.useLocation).disabled(model.foregroundOnly)
                    Button("位置情報の「常に許可」を要求") { model.requestAlwaysLocation() }.disabled(model.foregroundOnly)
                    Text("開始中のみバックグラウンド位置情報更新を利用します。位置情報自体は保存・送信しません。バッテリー消費が増えます。停止中や強制終了後の受信は行いません。")
                        .font(.caption)
                    if model.foregroundOnly { Text("前景専用ビルドです。バックグラウンド機能は無効です。") }
                }
                Section("音声") {
                    Text("playback / spokenAudio / duckOthers")
                    Text("音声取得後にセッションを有効化し、キュー終了後にnotifyOthersOnDeactivationで解放します。Spotifyの停止・音量は直接操作しません。")
                        .font(.caption)
                }
            }
            .navigationTitle("設定")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完了") { showSettings = false } } }
        }
    }
}
