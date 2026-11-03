import SwiftUI
import AVFoundation
import CoreLocation
import UIKit

@MainActor
final class ReceiverModel: NSObject, ObservableObject {
    static let workerURL = "https://vcm-queue.chibiken.workers.dev"
    static let demoURL = "https://muse-livetrack-receiver-20261101.sunny-moon-4267.chatgpt.site/demo-queue.json"
    @Published var queueURL: String { didSet { defaults.set(queueURL, forKey: "queueURL") } }
    @Published var interval: Double { didSet { defaults.set(interval, forKey: "interval") } }
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
        let token = runToken
        updateIdleTimer(); updateLocation()
        log("受信開始", "間隔 \(Int(interval))秒")
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self, self.running, self.runToken == token else { return }
                await self.poll(source, token: token)
                do { try await Task.sleep(for: .seconds(self.interval)) } catch { return }
            }
        }
    }

    func stop() {
        running = false; runToken = UUID()
        pollTask?.cancel(); pollTask = nil
        playbackTask?.cancel()
        player?.stop()
        if let current { setStatus(current, "停止（未完了）") }
        resolvePlayback(false)
        pending.removeAll()
        paused = false; resumeAllowed = true
        releaseAudio()
        updateCount(); updateIdleTimer(); updateLocation()
        log("受信停止")
    }

    func testPlay() {
        guard !busy, pending.isEmpty, !interruptionActive else {
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
            for message in messages {
                let key = MessageKey(source: source.absoluteString, session: queue.session, id: message.id)
                if !history.contains(where: { $0.id == key }) {
                    let status = message.audio == nil ? "音声なし" : (ledger.played.contains(key) ? "再生済み" : "待機中")
                    history.insert(HistoryEntry(id: key, message: message, status: status), at: 0)
                }
                guard let audio = message.audio, ledger.accept(key) else { continue }
                log("新規メッセージ検出", message.id)
                if current == nil { latestText = message.text }
                pending.append(PlaybackItem(message: message, key: key, url: QueueURL.audio(audio, relativeTo: source)))
            }
            if current == nil, pending.isEmpty, let last = messages.last { latestText = last.text }
            lastPoll = Date(); log("ポーリング成功", "\(queue.session) / \(messages.count)件")
            updateCount(); drain()
        } catch {
            if !Task.isCancelled, token == runToken { log("ポーリング失敗", error.localizedDescription) }
        }
    }

    private func drain() {
        guard playbackTask == nil, !pending.isEmpty, !paused, !interruptionActive else { return }
        busy = true
        playbackTask = Task { [weak self] in
            guard let self else { return }
            defer {
                self.player = nil; self.current = nil
                self.releaseAudio(); self.busy = false; self.playbackTask = nil; self.updateCount()
            }
            while !self.pending.isEmpty, !Task.isCancelled {
                let item = self.pending.removeFirst()
                self.current = item; self.latestText = item.message.text; self.updateCount()
                self.setStatus(item, "読み込み中")
                do {
                    let began = Date()
                    let success = try await self.playMP3(item)
                    try Task.checkCancellation()
                    let elapsed = String(format: "%.2f", Date().timeIntervalSince(began))
                    if success {
                        self.log("再生終了", "\(item.message.id) / \(elapsed)秒（割り込み待ち含む）")
                        self.setStatus(item, "再生済み")
                        if let key = item.key {
                            self.ledger.complete(key)
                            self.defaults.set(try JSONEncoder().encode(self.ledger.played), forKey: "played.v1")
                        }
                    } else { throw ReceiverError.message("再生が正常終了しませんでした / \(elapsed)秒") }
                } catch {
                    if Task.isCancelled { break }
                    self.log("再生エラー", "\(item.message.id): \(error.localizedDescription)")
                    self.setStatus(item, "スキップ（再生エラー）")
                    self.player?.stop(); self.releaseAudio()
                }
                self.player = nil; self.current = nil; self.updateCount()
                if !self.pending.isEmpty {
                    do { try await Task.sleep(for: .milliseconds(300)) } catch { break }
                }
            }
        }
    }

    private func playMP3(_ item: PlaybackItem) async throws -> Bool {
        guard let url = item.url else { throw ReceiverError.message("音声URLが不正です") }
        let data: Data
        if url.isFileURL { data = try Data(contentsOf: url) }
        else {
            let request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 5)
            let (download, response) = try await self.network.data(for: request)
            guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
                throw ReceiverError.message("音声HTTP \((response as? HTTPURLResponse)?.statusCode ?? 0)")
            }
            data = download
        }
        try Task.checkCancellation()
        while self.paused || self.interruptionActive { try await Task.sleep(for: .milliseconds(200)) }
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
                self?.stop(); self?.log("Audio Services リセット", "再度開始してください")
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
