import SwiftUI
import AVFoundation

struct ContentView: View {
    @StateObject private var model = ReceiverModel()
    @Environment(\.scenePhase) private var phase
    @State private var showSettings = false
    @AppStorage("showStartGuide") private var showStartGuide = true
    @State private var presentingGuide = false
    @State private var confirmReset = false
    private let accent = Color(red: 0.84, green: 0.97, blue: 0.67)
    private let background = Color(red: 0.06, green: 0.085, blue: 0.07)

    // Presentation-only grouping; the playback ledger and message order are unchanged.
    private struct RunKey: Hashable { let source: String; let session: String }
    private struct RunGroup: Identifiable {
        let id: RunKey
        let entries: [HistoryEntry]
        let date: Date?
    }
    private var runs: [RunGroup] {
        let grouped = Dictionary(grouping: model.history) { RunKey(source: $0.id.source, session: $0.id.session) }
        return grouped.map { key, entries in
            let sorted = entries.sorted { ($0.message.timestamp ?? .distantPast) > ($1.message.timestamp ?? .distantPast) }
            return RunGroup(id: key, entries: sorted, date: sorted.first?.message.timestamp)
        }.sorted {
            if $0.date != $1.date { return ($0.date ?? .distantPast) > ($1.date ?? .distantPast) }
            return ($0.id.source + $0.id.session) < ($1.id.source + $1.id.session)
        }
    }
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    (Text("Voice Coach").bold() + Text(" for Muse").foregroundColor(accent))
                        .font(.system(.largeTitle, design: .rounded))
                        .lineLimit(1).minimumScaleFactor(0.5)
                        .accessibilityAddTraits(.isHeader)
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Circle().frame(width: 14, height: 14)
                            Text(model.running ? "受信中" : "受信停止中").font(.title.bold())
                        }.foregroundStyle(model.running ? accent : Color.gray)
                        Button {
                            if model.running || model.busy {
                                model.stop()
                            } else {
                                model.start()
                                presentingGuide = model.running && showStartGuide
                            }
                        } label: {
                            Text(model.running ? "コーチング停止" : model.busy ? "音声テストを停止" : "コーチング開始")
                                .font(.title3.bold()).frame(maxWidth: .infinity).padding(.vertical, 12)
                        }
                        .buttonStyle(.borderedProminent).tint(accent).foregroundStyle(.black)
                    }
                    if model.paused {
                        Button("音声を再開") { model.resume() }
                            .buttonStyle(.borderedProminent).tint(.orange)
                    }
                    connectionStatus
                    if !model.notice.isEmpty { Text(model.notice).font(.footnote).foregroundStyle(accent) }
                    Divider()
                    HStack {
                        Text("コーチング履歴").font(.headline)
                        Spacer()
                        NavigationLink("すべて見る") { historyScreen }
                    }
                    if let run = runs.first {
                        runHeading(run, number: runs.count)
                        ForEach(Array(run.entries.prefix(3))) { entry in historyRow(entry) }
                    } else { Text("届いたコーチングがここに残ります。").foregroundStyle(.secondary) }
                    Divider()
                    Toggle("開始中は自動ロックを防止", isOn: $model.preventLock).tint(accent)
                    VStack(alignment: .leading, spacing: 8) {
                        Text("イヤホンをつなぎ、音楽を流しながら音声を確認できます。")
                            .font(.footnote).foregroundStyle(.secondary)
                        Button("音声テスト", systemImage: "speaker.wave.2") { model.testPlay() }
                            .disabled(model.busy || model.pendingCount > 0).buttonStyle(.bordered)
                    }
                }.padding(24)
            }
            .background(background)
            .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("設定", systemImage: "gearshape") { showSettings = true } } }
            .alert("Muse に LiveTrack を監視するよう指示してください", isPresented: $presentingGuide) {
                Button("OK", role: .cancel) { }
                Button("今後このダイアログは表示しない") { showStartGuide = false }
            } message: {
                Text("このアプリは受信専用です。Muse が LiveTrack の監視を始めると、コーチングが届きます。")
            }
            .sheet(isPresented: $showSettings) { settings }
        }
        .tint(accent)
        .preferredColorScheme(.dark)
        .onChange(of: phase) { _, new in model.sceneChanged(new) }
    }

    private var connectionStatus: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            VStack(alignment: .leading, spacing: 10) {
                LabeledContent("最終受信", value: model.lastPoll?.formatted(date: .omitted, time: .standard) ?? "まだ受信していません")
                LabeledContent("待機中の音声", value: "\(model.pendingCount) 件")
                if model.running {
                    if let last = model.lastPoll {
                        if context.date.timeIntervalSince(last) > model.interval + 15 {
                            Text("受信の更新が遅れています。通信状態を確認してください。")
                                .foregroundStyle(.orange)
                        }
                    } else { Text("接続先からの受信を待っています。詳細ログで状況を確認できます。") }
                }
            }.font(.subheadline).foregroundStyle(.secondary)
        }
    }

    private func runHeading(_ run: RunGroup, number: Int) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("ラン \(number)").font(.headline).foregroundStyle(accent)
            if let date = run.date {
                Text(date.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func historyRow(_ entry: HistoryEntry) -> some View {
        let waiting = entry.status == "待機中"
        return VStack(alignment: .leading, spacing: 6) {
            Text(entry.message.timestamp?.formatted(date: .omitted, time: .standard) ?? "時刻不明")
                .font(.caption).foregroundStyle(.secondary)
            Text(entry.message.text).font(.body)
            if waiting {
                Text("未再生 · 待機中").font(.caption.bold())
                    .padding(.horizontal, 9).padding(.vertical, 4)
                    .foregroundStyle(.black).background(accent, in: Capsule())
            } else {
                Text(entry.status).font(.caption).foregroundStyle(.secondary)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(waiting ? accent.opacity(0.14) : .clear, in: RoundedRectangle(cornerRadius: 10))
    }

    private var historyScreen: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 20) {
                if runs.isEmpty { Text("まだコーチングはありません。").foregroundStyle(.secondary) }
                ForEach(Array(runs.enumerated()), id: \.element.id) { index, run in
                    runHeading(run, number: runs.count - index)
                    ForEach(run.entries) { entry in historyRow(entry) }
                }
                if !model.history.isEmpty {
                    Button("再生済みの記録をリセット", role: .destructive) { confirmReset = true }
                        .disabled(model.busy || model.pendingCount > 0)
                    Text("履歴はアプリを開いている間の受信内容です。再生済みの記録は次回起動後も保持されます。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }.padding(24)
        }
        .background(background).navigationTitle("コーチング履歴").navigationBarTitleDisplayMode(.inline)
        .confirmationDialog("再生済みの記録をリセットしますか？", isPresented: $confirmReset, titleVisibility: .visible) {
            Button("リセットする", role: .destructive) { model.resetPlayed() }
            Button("キャンセル", role: .cancel) { }
        } message: {
            Text("すべてのランの記録を消去します。接続先に残っている音声は、次の受信時に再び再生されます。")
        }
    }

    private var logScreen: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("接続や音声の問題を調べるための記録です。時刻はUTCで表示します。")
                    .font(.footnote).foregroundStyle(.secondary)
                Text(model.logs.joined(separator: "\n"))
                    .font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }.padding(20)
        }
        .background(background).navigationTitle("詳細ログ").navigationBarTitleDisplayMode(.inline)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { Button("ログをコピー") { model.copyLogs() } } }
    }

    private var settings: some View {
        NavigationStack {
            Form {
                Section("接続先") {
                    TextField("ベースURL または queue.json のURL", text: $model.queueURL, axis: .vertical)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .disabled(model.settingsLocked)
                    Button("Worker に切り替える") { model.queueURL = ReceiverModel.workerURL }.disabled(model.settingsLocked)
                    Button("旧 XServer に切り替える") { model.queueURL = "https://discoverroutes.jp/livetrack/" }.disabled(model.settingsLocked)
                    Button("サンプルURLで試す") { model.queueURL = ReceiverModel.demoURL }.disabled(model.settingsLocked)
                    Text(model.settingsLocked
                         ? "接続先を変更するには、メイン画面で受信・再生を停止してください。"
                         : "コーチングを受け取るURLを指定します。設定は自動保存されます。変更後はメイン画面で開始し、最終受信時刻を確認してください。")
                        .font(.caption)
                    Text("サンプルでは3件の短い確認音を受信できます。設定済みの接続先URLは置き換わります。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("案内") {
                    Toggle("開始時の案内を表示", isOn: $showStartGuide)
                }
                Section("バックグラウンド受信") {
                    Toggle("開始中に位置情報更新を利用", isOn: $model.useLocation).disabled(model.foregroundOnly)
                    Button("位置情報の「常に許可」を設定") { model.requestAlwaysLocation() }.disabled(model.foregroundOnly)
                    Text("画面を閉じた状態でも受信を続けるため、開始中だけ位置情報更新を利用します。位置情報は保存・送信しません。バッテリー消費が増え、受信の継続は端末の状態によって異なります。")
                        .font(.caption)
                    if model.foregroundOnly { Text("このバージョンはアプリを表示している間のみ利用できます。") }
                }
                Section("再生方法") {
                    Picker("再生方法", selection: $model.mode) {
                        ForEach(PlaybackMode.allCases, id: \.self) { Text($0.rawValue).tag($0) }
                    }
                    if model.mode != .muse {
                        let voices = model.voices
                        Picker("日本語の声", selection: $model.voiceID) {
                            Text("自動（最高品質）").tag("")
                            ForEach(voices, id: \.identifier) { voice in
                                Text("\(voice.name)（\(voice.quality == .premium ? "プレミアム" : voice.quality == .enhanced ? "拡張" : "標準")）").tag(voice.identifier)
                            }
                        }
                        if !voices.contains(where: { $0.quality == .premium || $0.quality == .enhanced }) {
                            Text("高品質の日本語音声がありません。設定アプリ → アクセシビリティ → 読み上げコンテンツ → 声 → 日本語からダウンロードできます。")
                                .font(.caption)
                        }
                        Stepper("読み上げ速度：\(model.speechSpeed, specifier: "%.1f")倍", value: $model.speechSpeed, in: 0.8...1.2, step: 0.1)
                    }
                }.disabled(model.settingsLocked)
                Section("音声") {
                    Text("他のアプリの音量を一時的に下げてコーチングを再生し、終了後に元の音量へ戻します。")
                    Text("接続中のイヤホンなど、iPhoneで選択されている出力先から再生します。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section {
                    DisclosureGroup("詳細設定") {
                        Stepper("受信の確認間隔：\(Int(model.interval))秒", value: $model.interval, in: 1...300, step: 1)
                            .disabled(model.settingsLocked)
                        Text("通常は5秒のままで利用できます。変更する場合は受信・再生を停止してください。")
                            .font(.caption)
                    }
                    NavigationLink("詳細ログ") { logScreen }
                }
            }
            .navigationTitle("設定")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完了") { showSettings = false } } }
        }.tint(accent)
    }
}
