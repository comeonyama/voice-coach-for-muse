import Foundation

struct CoachingMessage: Codable {
    let id: String
    let ts: String
    let audio: String?
    let text: String

    func isStale(at now: Date = Date()) -> Bool {
        guard let timestamp else { return true }
        return now.timeIntervalSince(timestamp) >= 10 * 60
    }

    var timestamp: Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: ts) ?? ISO8601DateFormatter().date(from: ts)
    }
}

struct CoachingQueue: Decodable {
    let session: String
    let messages: [CoachingMessage]
}

struct MessageKey: Codable, Hashable {
    let source: String
    let session: String
    let id: String
}

struct PlaybackItem: Identifiable {
    let id: UUID = UUID()
    let message: CoachingMessage
    let key: MessageKey?
    let url: URL?
    var manualRetry = false
    var isTest: Bool { key == nil }
}

struct HistoryEntry: Identifiable {
    let id: MessageKey
    let message: CoachingMessage
    var status: String
}

struct HistoryArchive: Decodable {
    struct Run: Decodable {
        let session: String
        let date: String
        let messages: [CoachingMessage]
    }
    let runs: [Run]
}

struct HistoryRun: Identifiable {
    let id: String
    let date: String
    let entries: [HistoryEntry]

    static func merged(source: String, live: [HistoryEntry], activeSession: String?,
                       archive: HistoryArchive?) -> [HistoryRun] {
        var groups = [String: [String: HistoryEntry]]()
        var dates = [String: String]()
        for run in archive?.runs ?? [] {
            dates[run.session] = run.date
            for message in run.messages {
                let key = MessageKey(source: source, session: run.session, id: message.id)
                groups[run.session, default: [:]][message.id] = HistoryEntry(id: key, message: message, status: "再生済み")
            }
        }
        for entry in live where entry.id.source == source {
            let session = entry.id.session
            if session == activeSession || groups[session]?[entry.id.id] == nil {
                groups[session, default: [:]][entry.id.id] = entry
            }
        }
        return groups.map { session, messages in
            let rows = messages.values.sorted {
                let a = $0.message.timestamp ?? .distantPast, b = $1.message.timestamp ?? .distantPast
                return a == b ? $0.id.id > $1.id.id : a > b
            }
            return HistoryRun(id: session, date: dates[session] ?? String(rows[0].message.ts.prefix(10)), entries: rows)
        }.sorted {
            if $0.date != $1.date { return $0.date > $1.date }
            let a = $0.entries[0].message.timestamp ?? .distantPast, b = $1.entries[0].message.timestamp ?? .distantPast
            return a == b ? $0.id > $1.id : a > b
        }
    }
}

/// Separate the persistent successful IDs from this run's attempted IDs.
struct QueueLedger {
    private(set) var played: Set<MessageKey>
    private(set) var seen = Set<MessageKey>()

    init(played: Set<MessageKey> = []) { self.played = played }

    mutating func accept(_ key: MessageKey) -> Bool {
        guard !played.contains(key), !seen.contains(key) else { return false }
        seen.insert(key)
        return true
    }

    mutating func complete(_ key: MessageKey) { played.insert(key) }
    mutating func resetAttempts() { seen.removeAll() }
    mutating func resetAll() { played.removeAll(); seen.removeAll() }
}

enum QueueURL {
    static func source(_ string: String) -> URL? {
        guard let url = URL(string: string.trimmingCharacters(in: .whitespacesAndNewlines)),
              ["https", "http"].contains(url.scheme?.lowercased() ?? ""), url.host != nil,
              url.user == nil, url.password == nil else { return nil }
        return url.pathExtension.lowercased() == "json" ? url : url.appendingPathComponent("queue.json")
    }

    static func audio(_ path: String, relativeTo base: URL) -> URL? {
        guard !path.isEmpty, let url = URL(string: path, relativeTo: base)?.absoluteURL,
              ["https", "http"].contains(url.scheme?.lowercased() ?? ""), url.host != nil,
              url.user == nil, url.password == nil else { return nil }
        return url
    }

    static func polling(_ base: URL, date: Date = Date()) -> URL {
        var parts = URLComponents(url: base, resolvingAgainstBaseURL: true)!
        var query = (parts.queryItems ?? []).filter { $0.name != "_muse_t" }
        query.append(URLQueryItem(name: "_muse_t", value: String(Int(date.timeIntervalSince1970 * 1000))))
        parts.queryItems = query
        return parts.url!
    }
}

enum LiveTrackKick {
    struct Reply: Decodable {
        let ok: Bool
        let found: Bool?
    }

    static func endpoint(_ raw: String, key: String) -> URL? {
        guard !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              var parts = URLComponents(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
              parts.scheme?.lowercased() == "https", let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil, parts.path.hasSuffix("/exec") else { return nil }
        var items = (parts.queryItems ?? []).filter { $0.name != "key" }
        items.append(URLQueryItem(name: "key", value: key))
        parts.queryItems = items
        parts.percentEncodedQuery = parts.percentEncodedQuery?.replacingOccurrences(of: "+", with: "%2B")
        parts.fragment = nil
        return parts.url
    }

    static func today(_ date: Date = Date(), zone: TimeZone = .current) -> String {
        let format = DateFormatter()
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = zone
        format.dateFormat = "yyyy-MM-dd"
        return "run-" + format.string(from: date)
    }
}
