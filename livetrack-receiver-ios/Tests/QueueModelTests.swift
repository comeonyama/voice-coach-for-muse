import Foundation

@main struct QueueModelTests {
    static func main() throws {
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
        print("PASS: relative URLs, invalid schemes, cache query, duplicate prevention, relaunch persistence, failed-item retry, session/source isolation, reset, JSON/date contract")
    }
}
