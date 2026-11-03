import SwiftUI
import AVFoundation
import CoreLocation
import UIKit

@MainActor
final class ReceiverModel: NSObject, ObservableObject {
    static let workerURL = "https://vcm-queue.chibiken.workers.dev"
    static let demoURL = "https://muse-livetrack-receiver-20261101.sunny-moon-4267.chatgpt.site/demo-queue.json"
    @Published var queueURL: String { didSet { defaults.set(queueURL, forKey: "queueURL") } }
    @Published var detectionURL: String { didSet { defaults.set(detectionURL, forKey: "detectionURL") } }
    @Published var detectionKey: String { didSet { defaults.set(detectionKey, forKey: "detectionKey") } }
    var detectionEnabled: Bool { LiveTrackKick.endpoint(detectionURL, key: detectionKey) != nil }
    private var kickTask: Task<Void, Never>?
    private var kickStart: ContinuousClock.Instant?
    @Published var interval: Double { didSet { defaults.set(interval, forKey: "interval") } }
    @Published var timeoutMinutes: Int { didSet { defaults.set(timeoutMinutes, forKey: "timeoutMinutes") } }
    private var timeoutTask: Task<Void, Never>?
    private var stoppingForTimeout = false
    private let speaker = AVSpeechSynthesizer()
    private var finalAnnouncement: AVSpeechUtterance?
    private var offline = false
    private var networkFailures = 0
    @Published var preventLock: Bool { didSet { defaults.set(preventLock, forKey: "preventLock"); updateIdleTimer() } }
    @Published var useLocation: Bool { didSet { defaults.set(useLocation, forKey: "useLocation"); updateLocation() } }
    @Published private(set) var running = false
    @Published private(set) var busy = false
    @Published private(set) var paused = false
    @Published private(set) var latestText = "準備ができたら開始してください。"
    @Published private(set) var lastPoll: Date?
    @Published private(set) var pendingCount = 0
    @Published private(set) var audioStatus = "未使用"
    @Published private(set) var locationStatus = "停止中"
    @Published private(set) var history: [HistoryEntry] = []
    @Published private var archives = [String: HistoryArchive]()
    @Published private var liveSession: String?
    var historyRuns: [HistoryRun] {
        guard let source = QueueURL.source(queueURL)?.absoluteString else { return [] }
        return HistoryRun.merged(source: source, live: history, activeSession: liveSession, archive: archives[source])
    }

    func refreshHistory() async {
        guard let source = QueueURL.source(queueURL) else { return }
        let url = source.deletingLastPathComponent().appendingPathComponent("history.json")
        do {
            let request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 10)
            let (data, response) = try await network.data(for: request)
            try Task.checkCancellation()
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else { return }
            archives[source.absoluteString] = try JSONDecoder().decode(HistoryArchive.self, from: data)
        } catch {
            if !Task.isCancelled { log("履歴取得失敗", error.localizedDescription) }
        }
    }

    @Published private(set) var logs: [String] = []
    @Published var notice = ""
    var settingsLocked: Bool { running || busy }
    var foregroundOnly: Bool {
        #if FOREGROUND_ONLY
        return true
        #else
        return false
        #endif
    }

    private let defaults: UserDefaults
    private var ledger: QueueLedger
    private let session = AVAudioSession.sharedInstance()
    private let location = CLLocationManager()
    private var pollTask: Task<Void, Never>?
    private var playbackTask: Task<Void, Never>?
    private var pending: [PlaybackItem] = []
    private var current: PlaybackItem?
    private var player: AVAudioPlayer?
    private var completion: CheckedContinuation<Bool, Never>?
    private var sessionActive = false
    private var interruptionActive = false
    private var resumeAllowed = true
    private var observers: [NSObjectProtocol] = []
    private var runToken = UUID()
    private var network: URLSession!

    override convenience init() { self.init(defaults: .standard) }

    init(defaults: UserDefaults) {
        self.defaults = defaults
        queueURL = defaults.string(forKey: "queueURL") ?? Self.workerURL
        detectionURL = defaults.string(forKey: "detectionURL") ?? ""
        detectionKey = defaults.string(forKey: "detectionKey") ?? ""
        timeoutMinutes = min(1440, max(1, defaults.object(forKey: "timeoutMinutes") as? Int ?? 30))
        let storedInterval = defaults.double(forKey: "interval")
        interval = storedInterval >= 1 && storedInterval <= 300 ? storedInterval : 5
        preventLock = defaults.object(forKey: "preventLock") as? Bool ?? true
        useLocation = defaults.object(forKey: "useLocation") as? Bool ?? true
        let saved = defaults.data(forKey: "played.v1")
        let played = saved.flatMap { try? JSONDecoder().decode(Set<MessageKey>.self, from: $0) } ?? []
        ledger = QueueLedger(played: played)
        super.init()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.timeoutIntervalForRequest = 5
        configuration.timeoutIntervalForResource = 10
        configuration.waitsForConnectivity = false
        network = URLSession(configuration: configuration)
        speaker.delegate = self
        speaker.usesApplicationAudioSession = false
        logs = defaults.stringArray(forKey: "playbackDiagnostics") ?? []
        location.delegate = self
        location.activityType = .fitness
        location.desiredAccuracy = kCLLocationAccuracyHundredMeters
        location.distanceFilter = 10
        location.pausesLocationUpdatesAutomatically = false
        observeNotifications()
        log("アプリ準備完了", foregroundOnly ? "前景専用構成" : "Audio / Location構成")
    }

    func log(_ event: String, _ detail: String = "") {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        logs.append("\(formatter.string(from: Date())) [\(event)] \(detail)")
        if logs.count > 2000 { logs.removeFirst(logs.count - 2000) }
    }

    func start() {
        guard !running, !busy else { return }
        guard let source = QueueURL.source(queueURL) else {
            notice = "設定で有効な配信URLを指定してください。https:// で始まるURLを推奨します。"; return
        }
        running = true; paused = false; resumeAllowed = true
        lastPoll = nil
        ledger.resetAttempts()
        runToken = UUID()
        offline = false; networkFailures = 0
        armTimeout()
        updateIdleTimer(); updateLocation()
        log("受信開始", "間隔 \(Int(interval))秒")
        kickStart = detectionEnabled ? .now : nil
        beginPolling(source)
    }

    private func beginPolling(_ source: URL) {
        pollTask?.cancel()
        let token = runToken
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, self.running, self.runToken == token else { return }
                await self.poll(source, token: token)
                guard !Task.isCancelled, self.running, self.runToken == token, !self.stoppingForTimeout else { return }
                self.startKickIfNeeded()
                do { try await Task.sleep(for: .seconds(self.interval)) } catch { return }
            }
        }
    }

    // Consume the start marker once, after the first queue request has returned.
    private func startKickIfNeeded() {
        guard let start = kickStart, let url = LiveTrackKick.endpoint(detectionURL, key: detectionKey) else { return }
        kickStart = nil
        let token = runToken
        kickTask = Task {
            let clock = ContinuousClock()
            let end = start.advanced(by: .seconds(1800))
            var warned = false
            while !Task.isCancelled, running, token == runToken, clock.now < end {
                let tick = clock.now
                if !warned && tick >= start.advanced(by: .seconds(900)) {
                    warned = true
                    announce("LiveTrack の開始メールが見つかりません")
                }
                do {
                    let request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 10)
                    let (data, response) = try await network.data(for: request)
                    try Task.checkCancellation()
                    if let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) {
                        let reply = try JSONDecoder().decode(LiveTrackKick.Reply.self, from: data)
                        if reply.ok && reply.found == true { return }
                    }
                } catch {
                    // URL errors may contain the request URL and key. Retry silently.
                    if Task.isCancelled { return }
                }
                do { try await clock.sleep(until: min(tick.advanced(by: .seconds(30)), end)) } catch { return }
            }
        }
    }

    private func cancelKick() {
        kickStart = nil
        kickTask?.cancel()
        kickTask = nil
    }

    func stop() {
        cancelKick()
        timeoutTask?.cancel(); timeoutTask = nil
        finalAnnouncement = nil; stoppingForTimeout = false
        speaker.stopSpeaking(at: .immediate)
        offline = false
        running = false; runToken = UUID()
        pollTask?.cancel(); pollTask = nil
        playbackTask?.cancel()
        player?.stop()
        if let current { setStatus(current, "停止（未完了）") }
        resolvePlayback(false)
        for item in pending { setStatus(item, "停止（未完了）") }
        pending.removeAll()
        paused = false; resumeAllowed = true
        releaseAudio()
        updateCount(); updateIdleTimer(); updateLocation()
        log("受信停止")
    }

    func testPlay() {
        guard !busy, pending.isEmpty, !interruptionActive, !offline, !stoppingForTimeout else {
            notice = "音声の再生や一時停止が終わってからお試しください。"; return
        }
        guard let url = Bundle.main.url(forResource: "test-coaching", withExtension: "mp3") ?? Bundle.main.url(forResource: "test-tone", withExtension: "mp3") else {
            log("再生エラー", "同梱MP3がありません"); return
        }
        let message = CoachingMessage(id: "test", ts: ISO8601DateFormatter().string(from: Date()), audio: "", text: "音声を確認しています。音楽の音量が下がり、終了後に元に戻るか確認してください。")
        pending.append(PlaybackItem(message: message, key: nil, url: url))
        drain()
    }

    func resetPlayed() {
        guard !busy, pending.isEmpty else { notice = "再生を停止してからリセットしてください。"; return }
        ledger.resetAll(); defaults.removeObject(forKey: "played.v1"); history.removeAll()
        notice = "すべてのランの再生済み記録を消去しました。"
        log("再生済みリセット")
    }

    func copyLogs() {
        UIPasteboard.general.string = logs.joined(separator: "\n")
        notice = "ログをコピーしました（時刻はUTC）。"
    }

    private func poll(_ source: URL, token: UUID) async {
        do {
            var request = URLRequest(url: QueueURL.polling(source), cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 10)
            request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
            let (data, response) = try await network.data(for: request)
            try Task.checkCancellation()
            guard running, token == runToken else { return }
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                throw ReceiverError.message("queue.json HTTP \((response as? HTTPURLResponse)?.statusCode ?? 0)")
            }
            let queue = try JSONDecoder().decode(CoachingQueue.self, from: data)
            guard !queue.session.isEmpty else { throw ReceiverError.message("sessionが空です") }
            let messages = queue.messages.enumerated().filter { entry in
                let valid = !entry.element.id.isEmpty && entry.element.timestamp != nil
                if !valid { log("メッセージ形式エラー", entry.element.id) }
                return valid
            }.sorted {
                let a = $0.element.timestamp!, b = $1.element.timestamp!
                return a == b ? $0.offset < $1.offset : a < b
            }.map(\.element)
            if (kickStart != nil || kickTask != nil) && queue.session == LiveTrackKick.today() { cancelKick() }
            liveSession = queue.session
            let firstSync = defaults.data(forKey: "played.v1") == nil && !defaults.bool(forKey: "queueSynced")
            var persistPlayed = firstSync
            let now = Date()
            var receivedNew = false
            for message in messages {
                let key = MessageKey(source: source.absoluteString, session: queue.session, id: message.id)
                let stale = message.isStale(at: now)
                let skip = firstSync || (stale && !ledger.seen.contains(key))
                let newlySkipped = skip && !ledger.played.contains(key)
                if newlySkipped {
                    ledger.complete(key)
                    persistPlayed = true
                }
                if !history.contains(where: { $0.id == key }) {
                    receivedNew = true
                    let status: String
                    if firstSync { status = "再生済み" }
                    else if message.audio == nil { status = "音声なし" }
                    else if newlySkipped { status = "スキップ（時間経過）" }
                    else { status = ledger.played.contains(key) ? "再生済み" : "待機中" }
                    history.insert(HistoryEntry(id: key, message: message, status: status), at: 0)
                }
                guard let audio = message.audio, ledger.accept(key) else { continue }
                log("新規メッセージ検出", message.id)
                if current == nil { latestText = message.text }
                let item = PlaybackItem(message: message, key: key, url: QueueURL.audio(audio, relativeTo: source))
                setStatus(item, "待機中")
                pending.append(item)
            }
            if persistPlayed { savePlayed() }
            if !defaults.bool(forKey: "queueSynced") { defaults.set(true, forKey: "queueSynced") }
            if receivedNew { armTimeout() }
            networkFailures = 0
            if offline {
                offline = false
                recoverBacklog(source: source, session: queue.session)
                announce("回線が復旧しました")
            }
            if current == nil, pending.isEmpty, let last = messages.last { latestText = last.text }
            lastPoll = Date(); log("ポーリング成功", "\(queue.session) / \(messages.count)件")
            updateCount(); drain()
        } catch {
            guard !Task.isCancelled, token == runToken, !stoppingForTimeout else { return }
            log("ポーリング失敗", error.localizedDescription)
            if error is URLError {
                networkFailures += 1
                if !offline && networkFailures >= 2 {
                    offline = true
                    suspendPlaybackForRecovery()
                    announce("回線がつながっていません")
                }
            } else { networkFailures = 0 }
        }
    }

    private func drain() {
        guard !offline, !stoppingForTimeout, playbackTask == nil, !pending.isEmpty, !paused, !interruptionActive else { return }
        busy = true
        playbackTask = Task { [weak self] in
            guard let self else { return }
            defer {
                self.player = nil; self.current = nil
                self.releaseAudio(); self.busy = false; self.playbackTask = nil; self.updateCount(); self.drain()
            }
            while !self.pending.isEmpty, !Task.isCancelled {
                let item = self.pending.removeFirst()
                if !item.manualRetry, let key = item.key, item.message.isStale() {
                    self.ledger.complete(key)
                    self.savePlayed()
                    self.setStatus(item, "スキップ（時間経過）")
                    continue
                }
                self.current = item; self.latestText = item.message.text; self.updateCount()
                self.setStatus(item, "読み込み中")
                do {
                    let began = Date()
                    try await self.playWithRecovery(item)
                    try Task.checkCancellation()
                    let elapsed = String(format: "%.2f", Date().timeIntervalSince(began))
                    self.log("再生終了", "\(item.message.id) / \(elapsed)秒（割り込み待ち含む）")
                    self.setStatus(item, "再生済み")
                    if let key = item.key {
                        self.ledger.complete(key)
                        self.savePlayed()
                    }
                } catch {
                    if Task.isCancelled { break }
                    self.log("再生エラー", "\(item.message.id): \(error.localizedDescription)")
                    self.setStatus(item, "再生エラー")
                    self.player?.stop(); self.releaseAudio()
                }
                self.player = nil; self.current = nil; self.updateCount()
                if !self.pending.isEmpty {
                    do { try await Task.sleep(for: .milliseconds(300)) } catch { break }
                }
            }
        }
    }

    private func announce(_ text: String) {
        log("システム通知", text)
        let speech = AVSpeechUtterance(string: text)
        speech.voice = AVSpeechSynthesisVoice(language: "ja-JP")
        if stoppingForTimeout { finalAnnouncement = speech }
        speaker.speak(speech)
    }

    private func armTimeout() {
        timeoutTask?.cancel()
        timeoutTask = Task {
            do { try await Task.sleep(for: .seconds(timeoutMinutes * 60)) } catch { return }
            guard running else { return }
            stoppingForTimeout = true
            cancelKick()
            pollTask?.cancel()
            suspendPlaybackForRecovery()
            speaker.stopSpeaking(at: .immediate)
            announce("\(timeoutMinutes)分間、Muse からの音声が届いていないので受信を停止します")
            // If the OS cannot finish the announcement, still honor automatic stop.
            do { try await Task.sleep(for: .seconds(20)) } catch { return }
            stop()
        }
    }

    private func suspendPlaybackForRecovery() {
        playbackTask?.cancel()
        if let item = current, let key = item.key, !ledger.played.contains(key) {
            pending.insert(item, at: 0)
            setStatus(item, "待機中")
        }
        player?.stop()
        resolvePlayback(false)
        releaseAudio()
    }

    private func recoverBacklog(source: URL, session: String) {
        for entry in history where entry.id.source == source.absoluteString && entry.id.session == session && entry.status == "再生エラー" {
            if let audio = entry.message.audio {
                pending.append(PlaybackItem(message: entry.message, key: entry.id, url: QueueURL.audio(audio, relativeTo: source)))
            }
        }
        let latest = pending.filter { $0.key?.session == session && $0.key?.source == source.absoluteString }
            .sorted { ($0.message.timestamp ?? .distantPast) < ($1.message.timestamp ?? .distantPast) }.last
        for item in pending where item.id != latest?.id {
            guard let key = item.key else { continue }
            ledger.complete(key)
            setStatus(item, "スキップ（回線復旧）")
        }
        savePlayed()
        pending = latest.map { [$0] } ?? []
        if let latest { setStatus(latest, "待機中") }
    }

    private func savePlayed() {
        defaults.set(try? JSONEncoder().encode(ledger.played), forKey: "played.v1")
    }

    func retry(_ entry: HistoryEntry) {
        guard !offline, !stoppingForTimeout,
              history.first(where: { $0.id == entry.id })?.status == "再生エラー",
              let source = URL(string: entry.id.source), let audio = entry.message.audio else { return }
        _ = ledger.accept(entry.id)
        let item = PlaybackItem(message: entry.message, key: entry.id, url: QueueURL.audio(audio, relativeTo: source), manualRetry: true)
        setStatus(item, "待機中")
        pending.append(item)
        updateCount(); drain()
    }

    private func playWithRecovery(_ item: PlaybackItem) async throws {
        let delays = [1, 3, 5]
        for attempt in 0...4 {
            try Task.checkCancellation()
            do {
                if try await playMP3(item) { return }
                throw ReceiverError.message("プレイヤーが正常終了しませんでした")
            } catch {
                try Task.checkCancellation()
                let reason = error as NSError
                log("音声再生失敗", "\(item.key?.session ?? "test") / \(item.message.id) / 試行\(attempt + 1) / \(reason.domain) \(reason.code) / \(reason.localizedDescription)")
                var diagnostics = defaults.stringArray(forKey: "playbackDiagnostics") ?? []
                if let last = logs.last { diagnostics.append(last) }
                defaults.set(Array(diagnostics.suffix(100)), forKey: "playbackDiagnostics")
                player?.stop(); player = nil; releaseAudio()
                guard !item.isTest, attempt < 4 else { throw error }
                if attempt < delays.count {
                    setStatus(item, "再試行中")
                    try await Task.sleep(for: .seconds(delays[attempt]))
                } else {
                    do {
                        try session.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
                    } catch { log("Audio Session 再設定失敗", error.localizedDescription) }
                    announce("再生エラーが発生しました。再初期化します")
                    if running, let source = QueueURL.source(queueURL) {
                        lastPoll = nil
                        beginPolling(source)
                    }
                    // playMP3 creates a fresh player and reactivates the session.
                }
            }
        }
    }

    private func playMP3(_ item: PlaybackItem) async throws -> Bool {
        guard let url = item.url else { throw ReceiverError.message("音声URLが不正です") }
        let data: Data
        if url.isFileURL { data = try Data(contentsOf: url) }
        else {
            var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 5)
            request.setValue("no-cache", forHTTPHeaderField: "Cache-Control")
            let (download, response) = try await self.network.data(for: request)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                throw ReceiverError.message("音声HTTP \((response as? HTTPURLResponse)?.statusCode ?? 0)")
            }
            data = download
        }
        try Task.checkCancellation()
        while self.paused || self.interruptionActive || self.speaker.isSpeaking { try await Task.sleep(for: .milliseconds(200)) }
        let audio = try AVAudioPlayer(data: data)
        audio.delegate = self
        guard audio.prepareToPlay() else { throw ReceiverError.message("prepareToPlay失敗") }
        self.player = audio
        try self.activateAudio()
        return await withCheckedContinuation { continuation in
            self.completion = continuation
            if audio.play() {
                self.log("再生開始", item.message.id); self.setStatus(item, "再生中")
            } else { self.resolvePlayback(false) }
        }
    }

    private func activateAudio() throws {
        guard !sessionActive else { return }
        try session.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        try session.setActive(true)
        sessionActive = true; audioStatus = "有効 · duckOthers"
        log("Audio Session 有効化", "playback / spokenAudio / duckOthers")
    }

    private func releaseAudio() {
        guard sessionActive else { return }
        do {
            try session.setActive(false, options: [.notifyOthersOnDeactivation])
            sessionActive = false; audioStatus = "解放済み"
            log("Audio Session 解放", "notifyOthersOnDeactivation")
        } catch {
            audioStatus = "解放失敗"; log("Audio Session 解放エラー", error.localizedDescription)
            // Keep sessionActive true, so Stop or the final cleanup can retry.
        }
    }

    private func resolvePlayback(_ success: Bool) {
        let callback = completion; completion = nil; callback?.resume(returning: success)
    }

    private func updateCount() { pendingCount = pending.count + (current == nil ? 0 : 1) }
    private func setStatus(_ item: PlaybackItem, _ value: String) {
        if let key = item.key, let index = history.firstIndex(where: { $0.id == key }) { history[index].status = value }
    }

    func resume() {
        guard !interruptionActive else { notice = "電話などの割り込み終了後に再開できます。"; return }
        resumeAllowed = true
        do {
            if let player, completion != nil {
                try activateAudio()
                guard player.play() else { throw ReceiverError.message("割り込み後の再開失敗") }
                log("再生再開", current?.message.id ?? "")
            }
            paused = false; drain()
        } catch {
            log("再生エラー", error.localizedDescription); paused = false; resolvePlayback(false); releaseAudio()
        }
    }

    private func observeNotifications() {
        let center = NotificationCenter.default
        observers.append(center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] notification in
            let type = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
            let options = notification.userInfo?[AVAudioSessionInterruptionOptionKey] as? UInt ?? 0
            Task { @MainActor [weak self] in self?.handleInterruption(type: type, options: options) }
        })
        observers.append(center.addObserver(forName: AVAudioSession.routeChangeNotification, object: nil, queue: .main) { [weak self] notification in
            let reason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.log("出力経路変更", "reason=\(reason ?? 0)")
                if reason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue, self.busy {
                    self.paused = true; self.player?.pause(); self.releaseAudio()
                    self.notice = "イヤホン接続が切れました。接続を確認して「音声を再開」を押してください。"
                }
            }
        })
        observers.append(center.addObserver(forName: AVAudioSession.mediaServicesWereResetNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.sessionActive = false
                self.resolvePlayback(false)
                self.log("Audio Services リセット", "次の再生でセッションを再設定")
            }
        })
    }

    private func handleInterruption(type: UInt?, options: UInt) {
        guard let type, let kind = AVAudioSession.InterruptionType(rawValue: type) else { return }
        if kind == .began {
            interruptionActive = true; resumeAllowed = !paused; paused = true
            player?.pause(); sessionActive = false; audioStatus = "割り込み中（OSが無効化）"
            log("割り込み開始", current?.message.id ?? "待機中")
        } else {
            interruptionActive = false
            let shouldResume = AVAudioSession.InterruptionOptions(rawValue: options).contains(.shouldResume)
            log("割り込み終了", "shouldResume=\(shouldResume)")
            if shouldResume && resumeAllowed { resume() }
            else { paused = true; notice = "割り込みが終了しました。「音声を再開」で続行できます。" }
        }
    }

    func sceneChanged(_ phase: ScenePhase) {
        log("アプリ表示状態", String(describing: phase))
        updateIdleTimer()
        if phase == .active { updateLocation() }
    }

    private func updateIdleTimer() { UIApplication.shared.isIdleTimerDisabled = running && preventLock }

    func requestAlwaysLocation() {
        guard !foregroundOnly else { return }
        if location.authorizationStatus == .notDetermined { location.requestWhenInUseAuthorization() }
        else if location.authorizationStatus == .authorizedWhenInUse { location.requestAlwaysAuthorization() }
        else { notice = "位置情報の許可はiPhoneの設定から変更できます。" }
    }

    private func updateLocation() {
        guard !foregroundOnly, running, useLocation else {
            location.stopUpdatingLocation(); location.allowsBackgroundLocationUpdates = false
            locationStatus = foregroundOnly ? "前景専用構成" : "停止中"; return
        }
        switch location.authorizationStatus {
        case .notDetermined:
            locationStatus = "許可待ち"; location.requestWhenInUseAuthorization()
        case .authorizedAlways, .authorizedWhenInUse:
            location.allowsBackgroundLocationUpdates = true
            location.showsBackgroundLocationIndicator = true
            location.startUpdatingLocation()
            locationStatus = location.authorizationStatus == .authorizedAlways ? "更新中 · 常に許可" : "更新中 · 使用中のみ許可"
        default:
            location.stopUpdatingLocation(); location.allowsBackgroundLocationUpdates = false
            locationStatus = "許可なし · 前景で利用してください"
        }
    }
}

extension ReceiverModel: AVSpeechSynthesizerDelegate {
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in
            if self.finalAnnouncement === utterance { self.stop() }
        }
    }
}

extension ReceiverModel: AVAudioPlayerDelegate {
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor [weak self] in
            guard let self, self.player === player else { return }
            self.resolvePlayback(flag)
        }
    }
    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        Task { @MainActor [weak self] in
            guard let self, self.player === player else { return }
            self.log("音声デコードエラー", error?.localizedDescription ?? "不明")
            self.resolvePlayback(false)
        }
    }
}

extension ReceiverModel: CLLocationManagerDelegate {
    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        Task { @MainActor [weak self] in self?.updateLocation() }
    }
    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        // Deliberately discard coordinates: do not store, log or transmit location.
    }
    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        Task { @MainActor [weak self] in self?.log("位置情報エラー", error.localizedDescription) }
    }
}

private enum ReceiverError: LocalizedError {
    case message(String)
    var errorDescription: String? { if case .message(let text) = self { return text }; return nil }
}
