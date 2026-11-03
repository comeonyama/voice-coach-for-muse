import Foundation

struct CoachingMessage: Codable {
    let id: String
    let ts: String
    let audio: String?
    let text: String

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
    var isTest: Bool { key == nil }
}

struct HistoryEntry: Identifiable {
    let id: MessageKey
    let message: CoachingMessage
    var status: String
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
