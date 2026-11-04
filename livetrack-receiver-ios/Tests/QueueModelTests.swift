import Foundation

@main struct QueueModelTests {
    static func main() throws {
        let start = StartRequest()
        let restoredStart = try JSONDecoder().decode(StartRequest.self, from: JSONEncoder().encode(start))
        precondition(restoredStart.id == start.id && restoredStart.since == start.since && restoredStart.phase == "waiting")
        let kick = LiveTrackKick.endpoint("https://example.com/exec?sid=old&since=old", key: "secret", sid: start.id, since: start.since)!
        let parameters = URLComponents(url: kick, resolvingAgainstBaseURL: false)!.queryItems!
        precondition(parameters.filter { $0.name == "sid" }.count == 1)
        precondition(parameters.first { $0.name == "sid" }?.value == start.id.uuidString)
        precondition(parameters.first { $0.name == "since" }?.value?.hasSuffix("Z") == true)
        let source = QueueURL.source("https://example.com/runs/queue.json?token=abc")!
        precondition(QueueURL.audio("audio/m1.mp3", relativeTo: source)?.absoluteString == "https://example.com/runs/audio/m1.mp3")
        precondition(QueueURL.source("file:///tmp/queue.json") == nil)
        precondition(QueueURL.source("https://user:pass@example.com/queue.json") == nil)
        precondition(QueueURL.audio("javascript:alert(1)", relativeTo: source) == nil)
        let poll = QueueURL.polling(source, date: Date(timeIntervalSince1970: 1))
        precondition(poll.absoluteString.contains("token=abc"))
        precondition(poll.absoluteString.contains("_muse_t=1000"))
        let key = MessageKey(source: source.absoluteString, session: "one", id: "m1")
        let otherSession = MessageKey(source: source.absoluteString, session: "two", id: "m1")
        let otherSource = MessageKey(source: "https://other.example/queue.json", session: "one", id: "m1")
        var ledger = QueueLedger()
        precondition(ledger.accept(key))
        precondition(!ledger.accept(key))
        precondition(ledger.played.isEmpty, "Detection must not mark playback successful")
        ledger.resetAttempts()
        precondition(ledger.accept(key), "Failed/skipped items can retry on next run")
        ledger.complete(key)
        let persisted = try JSONEncoder().encode(ledger.played)
        var restored = QueueLedger(played: try JSONDecoder().decode(Set<MessageKey>.self, from: persisted))
        precondition(!restored.accept(key), "Successful playback must survive relaunch")
        precondition(restored.accept(otherSession))
        precondition(restored.accept(otherSource))
        restored.resetAll()
        precondition(restored.accept(key))
        let json = #"{"session":"test","messages":[{"id":"a","ts":"2026-11-01T10:15:30+09:00","audio":"audio/a.mp3","text":"こんにちは"},{"id":"b","ts":"2026-11-01T10:15:30.123+09:00","audio":null,"text":"次です"}]}"#
        let queue = try JSONDecoder().decode(CoachingQueue.self, from: Data(json.utf8))
        precondition(QueueURL.source("https://example.com")?.path == "/queue.json")
        precondition(QueueURL.source("https://example.com/livetrack/")?.path == "/livetrack/queue.json")
        precondition(queue.messages.count == 2)
        precondition(queue.messages[0].audio == "audio/a.mp3")
        precondition(queue.messages[1].audio == nil)
        precondition(queue.messages.allSatisfy { $0.timestamp != nil })
        precondition(queue.messages[0].timestamp! < queue.messages[1].timestamp!)
        let message = queue.messages[0], stamp = message.timestamp!
        precondition(!message.isStale(at: stamp.addingTimeInterval(599)))
        precondition(message.isStale(at: stamp.addingTimeInterval(600)))
        let archiveJSON = #"{"runs":[{"session":"one","date":"2026-11-01","messages":[{"id":"a","ts":"2026-11-01T10:15:30+09:00","text":"archived"}]},{"session":"older","date":"2026-10-31","messages":[{"id":"a","ts":"2026-10-31T10:00:00+09:00","text":"old"}]}]}"#
        let archive = try JSONDecoder().decode(HistoryArchive.self, from: Data(archiveJSON.utf8))
        let live = HistoryEntry(id: MessageKey(source: source.absoluteString, session: "one", id: "a"), message: message, status: "待機中")
        let runs = HistoryRun.merged(source: source.absoluteString, live: [live], activeSession: "one", archive: archive)
        precondition(runs.map(\.id) == ["one", "older"])
        precondition(runs[0].entries.count == 1 && runs[0].entries[0].status == "待機中")
        precondition(runs[1].entries[0].status == "再生済み")
        precondition(HistoryRun.merged(source: source.absoluteString, live: [live], activeSession: "one", archive: nil).count == 1)
        let script = "https://example.com/macros/s/example/exec"
        precondition(LiveTrackKick.endpoint(script, key: "") == nil)
        precondition(LiveTrackKick.endpoint("", key: "test") == nil)
        let endpoint = LiveTrackKick.endpoint(script + "?key=old", key: "a+b&c")!
        precondition(endpoint.query == "key=a%2Bb%26c")
        let midnight = ISO8601DateFormatter().date(from: "2026-11-02T15:00:00Z")!
        precondition(LiveTrackKick.today(midnight, zone: TimeZone(secondsFromGMT: 32400)!) == "run-2026-11-03")
        print("PASS: relative URLs, invalid schemes, cache query, duplicate prevention, relaunch persistence, failed-item retry, session/source isolation, reset, JSON/date contract")
    }
}
