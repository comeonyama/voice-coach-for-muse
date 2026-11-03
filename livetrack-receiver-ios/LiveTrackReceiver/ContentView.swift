import SwiftUI

struct ContentView: View {
    @StateObject private var model = ReceiverModel()
    @Environment(\.scenePhase) private var phase
    @State private var detectionDraft = DetectionDraft()
    @State private var showSettings = false
    @AppStorage("showStartGuide") private var showStartGuide = true
    @State private var presentingGuide = false
    @State private var confirmReset = false
    private let accent = Color(red: 0, green: 0.4, blue: 0.93)
    private let green = Color(red: 0.02, green: 0.45, blue: 0.24)
    private let background = LinearGradient(colors: [Color(red: 0.84, green: 0.90, blue: 0.96), .white],
                                            startPoint: .top, endPoint: .bottom)
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 24) {
                    HStack {
                        Text("Voice Coach for Muse").font(.title.bold())
                            .lineLimit(1).minimumScaleFactor(0.55).accessibilityAddTraits(.isHeader)
                        Spacer()
                        Button {
                            detectionDraft.url = model.detectionURL
                            detectionDraft.key = model.detectionKey
                            showSettings = true
                        } label: {
                            Image(systemName: "gearshape.fill").font(.title2)
                                .frame(width: 44, height: 44)
                        }.foregroundStyle(.secondary).accessibilityLabel("設定")
                    }
                    powerControl
                    VStack(alignment: .leading, spacing: 12) {
                        HStack {
                            Text("コーチング履歴").font(.headline)
                            Spacer()
                            NavigationLink("すべて見る") { historyScreen }
                        }
                        if let latest = model.historyRuns.first {
                            runHeading(latest)
                            ForEach(Array(latest.entries.prefix(3))) { entry in historyRow(entry) }
                        } else {
                            Text("届いたコーチングがここに残ります。")
                                .font(.subheadline).foregroundStyle(.secondary).padding(.vertical, 12)
                        }
                    }
                    if let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
                       let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String {
                        Text("バージョン \(version) (\(build))")
                            .font(.caption2).foregroundStyle(.secondary)
                    }
                }.padding(.horizontal, 22).padding(.vertical, 12)
            }
            .background(background.ignoresSafeArea())
            .toolbar(.hidden, for: .navigationBar)
            .alert("Muse に LiveTrack を監視するよう指示してください", isPresented: $presentingGuide) {
                Button("OK", role: .cancel) { }
                Button("今後このダイアログは表示しない") { showStartGuide = false }
            } message: {
                Text("このアプリは受信専用です。Muse が LiveTrack の監視を始めると、コーチングが届きます。")
            }
            .safeAreaInset(edge: .bottom) {
                if model.paused || !model.notice.isEmpty {
                    HStack {
                        Text(model.notice).font(.footnote)
                        if model.paused { Button("音声を再開") { model.resume(); model.notice = "" } }
                        Button { model.notice = "" } label: { Image(systemName: "xmark.circle") }
                            .accessibilityLabel("閉じる").disabled(model.paused)
                    }.padding().background(.regularMaterial, in: RoundedRectangle(cornerRadius: 14)).padding(.horizontal)
                }
            }
            .sheet(isPresented: $showSettings, onDismiss: {
                if model.detectionURL != detectionDraft.url { model.detectionURL = detectionDraft.url }
                if model.detectionKey != detectionDraft.key { model.detectionKey = detectionDraft.key }
            }) { settings }
        }
        .tint(accent)
        .preferredColorScheme(.light)
        .task(id: "\(model.queueURL)|\(showSettings)|\(phase == .active)") {
            guard !showSettings, phase == .active else { return }
            while !Task.isCancelled {
                await model.refreshHistory()
                do { try await Task.sleep(for: .seconds(60)) } catch { return }
            }
        }
        .onChange(of: phase) { _, new in model.sceneChanged(new) }
    }

    private var powerControl: some View {
        VStack(spacing: 16) {
            Button {
                if model.running || model.busy { model.stop() }
                else {
                    model.start()
                    presentingGuide = model.running && showStartGuide && !model.detectionEnabled
                }
            } label: {
                ZStack {
                    Circle().fill(model.running ? accent.gradient : Color.white.gradient)
                        .shadow(color: .black.opacity(0.17), radius: 10, y: 6)
                    if !model.running {
                        Circle().fill(Color(white: 0.95).gradient).padding(23)
                    }
                    Image(systemName: "power").font(.system(size: 74, weight: .medium))
                        .foregroundStyle(model.running ? .white : Color(white: 0.70))
                }.frame(width: 176, height: 176)
            }
            .buttonStyle(.plain)
            .accessibilityLabel(model.running ? "コーチング停止" : model.busy ? "音声テストを停止" : "コーチング開始")
            HStack(spacing: 6) {
                if model.running { Circle().frame(width: 7, height: 7) }
                Text(model.running ? "受信中" : "受信停止中")
            }.font(.headline).foregroundStyle(model.running ? green : .gray)
        }.padding(.bottom, 6)
    }

    private func historyRow(_ entry: HistoryEntry) -> some View {
        let waiting = entry.status == "待機中"
        let failed = entry.status == "再生エラー"
        let played = entry.status == "再生済み"
        let card = RoundedRectangle(cornerRadius: 18)
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 4) {
                if failed { Image(systemName: "exclamationmark.circle.fill") }
                if played { Image(systemName: "checkmark.circle.fill") }
                Text(entry.status)
            }
            .font(.caption.weight(.semibold))
            .padding(.horizontal, 9).padding(.vertical, 4)
            .foregroundStyle(waiting ? .white : failed ? .red : played ? green : .secondary)
            .background(waiting ? accent : failed ? Color.red.opacity(0.1) : played ? green.opacity(0.12) : Color.gray.opacity(0.12), in: Capsule())
            .frame(maxWidth: .infinity, alignment: .trailing)
            Text(entry.message.text).font(.headline)
            if failed { Button("再試行", systemImage: "arrow.clockwise") { model.retry(entry) } }
            Text(entry.message.timestamp?.formatted(date: .abbreviated, time: .shortened) ?? "時刻不明")
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(16)
        .background(.white, in: card)
        .overlay { if waiting { card.strokeBorder(accent, lineWidth: 2) } }
        .shadow(color: .black.opacity(0.09), radius: 8, y: 4)
    }

    private func runHeading(_ run: HistoryRun) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(run.date).font(.subheadline.bold())
            Text(run.id).font(.caption).foregroundStyle(.secondary)
        }.accessibilityElement(children: .combine)
    }

    private var historyScreen: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 20) {
                if model.historyRuns.isEmpty { Text("まだコーチングはありません。").foregroundStyle(.secondary) }
                ForEach(model.historyRuns) { run in
                    runHeading(run)
                    ForEach(run.entries) { entry in historyRow(entry) }
                }
                if !model.history.isEmpty {
                    Button("再生済みの記録をリセット", role: .destructive) { confirmReset = true }
                        .disabled(model.busy || model.pendingCount > 0)
                    Text("過去ランの履歴は配信先から取得します。リセットは端末の再生済み記録に適用されます。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }.padding(24)
        }
        .background(background).navigationTitle("コーチング履歴").navigationBarTitleDisplayMode(.inline)
        .confirmationDialog("再生済みの記録をリセットしますか？", isPresented: $confirmReset, titleVisibility: .visible) {
            Button("リセットする", role: .destructive) { model.resetPlayed() }
            Button("キャンセル", role: .cancel) { }
        } message: {
            Text("すべてのランの記録を消去します。直近10分の音声は、次の受信時に再び再生されることがあります。")
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
        @Bindable var draft = detectionDraft
        return NavigationStack {
            Form {
                Section("接続先") {
                    TextField("ベースURL または queue.json のURL", text: $model.queueURL, axis: .vertical)
                        .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                        .disabled(model.settingsLocked)
                    Button("Worker に切り替える") { model.queueURL = ReceiverModel.workerURL }.disabled(model.settingsLocked)
                    Button("サンプルURLで試す") { model.queueURL = ReceiverModel.demoURL }.disabled(model.settingsLocked)
                    Text(model.settingsLocked
                         ? "接続先を変更するには、メイン画面で受信・再生を停止してください。"
                         : "コーチングを受け取るURLを指定します。設定は自動保存されます。変更後はメイン画面で開始し、最終受信時刻を確認してください。")
                        .font(.caption)
                    Text("サンプルでは3件の短い確認音を受信できます。設定済みの接続先URLは置き換わります。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("受信タイムアウト") {
                    Stepper("新着なしで自動停止：\(model.timeoutMinutes)分", value: $model.timeoutMinutes, in: 1...1440)
                        .disabled(model.settingsLocked)
                }
                Section {
                    DetectionField(title: "検知スクリプト URL", prompt: "https://…/exec", keyboard: .URL, text: $draft.url)
                    DetectionField(title: "検知スクリプト キー", prompt: "キー", keyboard: .asciiCapable, text: $draft.key)
                } header: {
                    Text("LiveTrack 開始検知")
                } footer: {
                    Text("GAS の /exec URL とキーを両方設定すると、受信開始後に開始メールを自動検知します。")
                }
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .disabled(model.settingsLocked)
                Section("案内") {
                    Toggle("開始時の案内を表示", isOn: $showStartGuide)
                }
                Section("バックグラウンド受信") {
                    Toggle("開始中は自動ロックを防止", isOn: $model.preventLock)
                    Toggle("開始中に位置情報更新を利用", isOn: $model.useLocation).disabled(model.foregroundOnly)
                    Button("位置情報の「常に許可」を設定") { model.requestAlwaysLocation() }.disabled(model.foregroundOnly)
                    Text("画面を閉じた状態でも受信を続けるため、開始中だけ位置情報更新を利用します。位置情報は保存・送信しません。バッテリー消費が増え、受信の継続は端末の状態によって異なります。")
                        .font(.caption)
                    if model.foregroundOnly { Text("このバージョンはアプリを表示している間のみ利用できます。") }
                }
                Section {
                    Button("音声テスト", systemImage: "speaker.wave.2") { model.testPlay() }
                        .disabled(model.busy || model.pendingCount > 0)
                } header: {
                    Text("サウンドチェック")
                } footer: {
                    Text("音楽を流しながら、イヤホンで音量の変化を確認できます。")
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

@Observable private final class DetectionDraft {
    var url = ""
    var key = ""
}

private struct DetectionField: View {
    let title: String
    let prompt: String
    let keyboard: UIKeyboardType
    @Binding var text: String
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title).font(.subheadline)
                    .onTapGesture { focused = true }
                Spacer()
                Button {
                    UIPasteboard.general.string = text
                } label: {
                    Image(systemName: "doc.on.doc").frame(width: 44, height: 44)
                }
                .buttonStyle(.borderless).disabled(text.isEmpty)
                .accessibilityLabel("\(title)をコピー")
                PasteButton(payloadType: String.self) { values in
                    if let value = values.first { text = value }
                }
                .labelStyle(.iconOnly)
                .accessibilityLabel("\(title)にペースト")
            }
            TextField(prompt, text: $text)
                .accessibilityLabel(title)
                .keyboardType(keyboard)
                .focused($focused)
                .padding(.horizontal, 10)
                .frame(maxWidth: .infinity, minHeight: 44)
                .background(Color(uiColor: .tertiarySystemFill), in: RoundedRectangle(cornerRadius: 8))
                .contentShape(Rectangle())
                .simultaneousGesture(TapGesture().onEnded { focused = true })
        }
    }
}
