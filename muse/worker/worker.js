// VCM (Voice Coach for Muse) queue worker.
//
// 配信基盤の Cloudflare 版 (旧 XServer 構成から移行)。公開側の
// 読み取りと、トークン認証付きの書き込みを
// 1 つの Worker で担い、実データは R2 に置く。
//
//   GET  /queue.json        -> R2 の "queue.json" をそのまま返す (no-store)
//   GET  /history.json      -> R2 の "history.json" をそのまま返す (no-store)
//   GET  /session.json      -> R2 の "session.json" (要 X-Upload-Token、非公開)
//   GET  /audio/<name>.mp3  -> R2 の "audio/<name>.mp3" を返す (no-store)
//   POST /upload?op=audio&name=<id>.mp3   (要 X-Upload-Token)
//   POST /upload?op=queue                 (要 X-Upload-Token)
//   POST /upload?op=history               (要 X-Upload-Token)
//   POST /upload?op=session               (要 X-Upload-Token)
//   POST /upload?op=cleanup               (要 X-Upload-Token)
//
// session.json は LiveTrack のセッション ID とトークン (認証情報) を含むため
// 公開読み取りはしない。書き込みは GAS (開始メール検知) から、読み取りは
// VM 上の常駐ウォッチャーから、いずれも X-Upload-Token 認証で行う。
//
// 書き込み契約は旧 upload.php と互換に保つ:
//   - 認証は X-Upload-Token ヘッダー (値は Worker の secret UPLOAD_TOKEN)
//   - op=audio は本体が MP3 バイナリ、上限 2,097,152 バイト (超過は 413)
//   - op=queue は queue 全体の JSON で置き換える
//   - op=cleanup は {"min_age_sec": n} を受け取り、queue から参照されて
//     いない MP3 のうち n 秒より古いものを削除する
//
// R2 は強い整合性なので、PUT 直後の GET で新しい内容が読める。
// queue.json は設計上公開読み取り可 (コーチング文と音声名のみを入れ、
// 個人情報やトークンは入れないこと)。
//
// 必要なバインディング:
//   QUEUE_BUCKET : R2 bucket
//   UPLOAD_TOKEN : secret (wrangler secret put UPLOAD_TOKEN)

const MAX_AUDIO_BYTES = 2097152;
const AUDIO_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]*\.mp3$/;

function json(status, obj) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}

function checkToken(request, env) {
  const token = request.headers.get("X-Upload-Token");
  return token !== null && env.UPLOAD_TOKEN && token === env.UPLOAD_TOKEN;
}

async function readQueue(env) {
  const obj = await env.QUEUE_BUCKET.get("queue.json");
  if (obj === null) throw new Error("queue unavailable");
  try {
    const queue = JSON.parse(await obj.text());
    if (!queue || !Array.isArray(queue.messages) || typeof queue.session !== "string"
        || queue.messages.some(m => !m || typeof m.id !== "string"
          || !(m.audio === null || typeof m.audio === "string"))) {
      throw new Error("invalid queue");
    }
    return queue;
  } catch {
    throw new Error("queue unreadable");
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/start-status.json") {
      const obj = await env.QUEUE_BUCKET.get("start-status.json");
      if (!obj) return json(404, { ok: false, error: "no start status" });
      const value = JSON.parse(await obj.text());
      return json(200, { start_id: value.start_id, state: value.state, at: value.at });
    }

    // ---- public reads -------------------------------------------------
    if (request.method === "GET" && url.pathname === "/queue.json") {
      const obj = await env.QUEUE_BUCKET.get("queue.json");
      if (obj === null) {
        return json(200, { session: "standby", messages: [] });
      }
      return new Response(obj.body, {
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
        },
      });
    }

    if (request.method === "GET" && url.pathname === "/history.json") {
      const obj = await env.QUEUE_BUCKET.get("history.json");
      if (obj === null) {
        return json(200, { runs: [] });
      }
      return new Response(obj.body, {
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
        },
      });
    }

    if (request.method === "GET" && url.pathname === "/session.json") {
      if (!checkToken(request, env)) {
        return json(401, { ok: false, error: "unauthorized" });
      }
      const obj = await env.QUEUE_BUCKET.get("session.json");
      if (obj === null) {
        return json(404, { ok: false, error: "no session" });
      }
      return new Response(obj.body, {
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
        },
      });
    }

    if (request.method === "GET" && url.pathname.startsWith("/audio/")) {
      const name = url.pathname.slice("/audio/".length);
      if (!AUDIO_NAME_RE.test(name)) return json(400, { ok: false, error: "bad name" });
      const obj = await env.QUEUE_BUCKET.get(`audio/${name}`);
      if (obj === null) return json(404, { ok: false, error: "not found" });
      return new Response(obj.body, {
        headers: {
          "Content-Type": "audio/mpeg",
          "Cache-Control": "no-store",
        },
      });
    }

    // ---- authenticated writes ----------------------------------------
    if (request.method === "POST" && url.pathname === "/upload") {
      if (!checkToken(request, env)) {
        return json(401, { ok: false, error: "unauthorized" });
      }
      const op = url.searchParams.get("op");

      if (op === "audio") {
        const name = url.searchParams.get("name") || "";
        if (!AUDIO_NAME_RE.test(name)) {
          return json(400, { ok: false, error: "bad name" });
        }
        const body = await request.arrayBuffer();
        if (body.byteLength === 0) {
          return json(400, { ok: false, error: "empty body" });
        }
        if (body.byteLength > MAX_AUDIO_BYTES) {
          return json(413, { ok: false, error: "too large" });
        }
        await env.QUEUE_BUCKET.put(`audio/${name}`, body, {
          httpMetadata: { contentType: "audio/mpeg" },
        });
        return json(200, { ok: true, name, bytes: body.byteLength });
      }

      if (op === "queue") {
        const text = await request.text();
        let parsed;
        try {
          parsed = JSON.parse(text);
        } catch {
          return json(400, { ok: false, error: "invalid json" });
        }
        if (typeof parsed.session !== "string" || !Array.isArray(parsed.messages)) {
          return json(400, { ok: false, error: "invalid queue shape" });
        }
        await env.QUEUE_BUCKET.put("queue.json", text, {
          httpMetadata: { contentType: "application/json" },
        });
        return json(200, { ok: true, messages: parsed.messages.length });
      }

      if (op === "history") {
        const text = await request.text();
        let parsed;
        try {
          parsed = JSON.parse(text);
        } catch {
          return json(400, { ok: false, error: "invalid json" });
        }
        if (!Array.isArray(parsed.runs)) {
          return json(400, { ok: false, error: "invalid history shape" });
        }
        for (const run of parsed.runs) {
          if (typeof run.session !== "string" || !Array.isArray(run.messages)) {
            return json(400, { ok: false, error: "invalid run shape" });
          }
        }
        await env.QUEUE_BUCKET.put("history.json", text, {
          httpMetadata: { contentType: "application/json" },
        });
        return json(200, { ok: true, runs: parsed.runs.length });
      }

      if (op === "start-status") {
        let value;
        try { value = await request.json(); } catch { return json(400, {ok:false, error:"invalid json"}); }
        if (!value || typeof value.start_id !== "string" ||
            !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value.start_id) ||
            !["monitoring", "closed"].includes(value.state) ||
            typeof value.at !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/.test(value.at) || !Number.isFinite(Date.parse(value.at)))
          return json(400, {ok:false, error:"invalid start status"});
        const safe = {start_id:value.start_id, state:value.state, at:value.at};
        await env.QUEUE_BUCKET.put("start-status.json", JSON.stringify(safe), {
          httpMetadata: {contentType:"application/json"}
        });
        return json(200, {ok:true});
      }

      if (op === "session") {
        const text = await request.text();
        let parsed;
        try {
          parsed = JSON.parse(text);
        } catch {
          return json(400, { ok: false, error: "invalid json" });
        }
        if (typeof parsed.session_id !== "string" || typeof parsed.token !== "string") {
          return json(400, { ok: false, error: "invalid session shape" });
        }
        parsed.received_at = new Date().toISOString();
        await env.QUEUE_BUCKET.put("session.json", JSON.stringify(parsed), {
          httpMetadata: { contentType: "application/json" },
        });
        return json(200, { ok: true, session_id: parsed.session_id });
      }

      if (op === "cleanup") {
        let minAgeSec = 3600;
        try {
          const body = await request.json();
          if (Number.isFinite(body.min_age_sec)) minAgeSec = Math.max(300, body.min_age_sec);
        } catch {
          // body 省略時はデフォルトの猶予を使う
        }
        let queue;
        try { queue = await readQueue(env); }
        catch { return json(409, { ok: false, error: "cleanup requires a valid queue" }); }
        const referenced = new Set(
          (queue.messages || []).map((m) => m.audio).filter(Boolean)
        );
        const cutoff = Date.now() - minAgeSec * 1000;
        const deleted = [];
        let cursor;
        do {
          const page = await env.QUEUE_BUCKET.list({ prefix: "audio/", cursor });
          for (const obj of page.objects) {
            if (referenced.has(obj.key)) continue;
            if (obj.uploaded.getTime() > cutoff) continue;
            // Recheck both references and upload age after listing.
            let latest;
            try { latest = await readQueue(env); }
            catch { return json(409, { ok: false, error: "queue changed or unreadable" }); }
            if (latest.messages.some(m => m.audio === obj.key)) continue;
            const head = await env.QUEUE_BUCKET.head(obj.key);
            if (!head || head.uploaded.getTime() > cutoff) continue;
            await env.QUEUE_BUCKET.delete(obj.key);
            deleted.push(obj.key);
          }
          cursor = page.truncated ? page.cursor : undefined;
        } while (cursor);
        return json(200, { ok: true, deleted, count: deleted.length });
      }

      return json(400, { ok: false, error: "unknown op" });
    }

    return json(404, { ok: false, error: "not found" });
  },
};
