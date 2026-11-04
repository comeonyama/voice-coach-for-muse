/**
 * VCM LiveTrack Detector (Google Apps Script)
 *
 * Garmin の LiveTrack 開始メールを Gmail から検知し、セッション URL
 * (session_id / token) を抽出して Cloudflare Worker (vcm-queue) に渡す。
 *
 * 使い方:
 *   - Web アプリとしてデプロイすると、iOS アプリから URL を叩いて
 *     起動できる (doGet)。?key=<KICK_SECRET> が必須。
 *   - scheduledCheck() を時間主導トリガーに登録すれば、キックなしでも
 *     定期検知できる (フォールバック用)。
 *
 * スクリプトプロパティ (プロジェクトの設定 > スクリプト プロパティ):
 *   KICK_SECRET   : アプリからのキック用シークレット (自分で決める)
 *   UPLOAD_TOKEN  : Worker の secret と同じ値
 *   WORKER_BASE   : 必須。自分の Worker の URL を設定すること
 *   PROCESSED_MAIL_IDS : 自動管理。処理済みメールIDのJSON配列（直近20件）
 */

var URL_RE = /livetrack\.garmin\.com\/session\/([0-9a-fA-F-]{36})\/token\/([0-9a-fA-F]+)/;
var SHORT_RE = /https?:\/\/gar\.mn\/[A-Za-z0-9]+/g;
// これより古い開始メールは拾わない (同日昼のランなど、過去分の誤検知防止)
var MAX_MAIL_AGE_MIN = 30;

function props_() {
  return PropertiesService.getScriptProperties();
}

function workerBase_() {
  var base = (props_().getProperty('WORKER_BASE') || '').trim();
  if (!base) {
    Logger.log('WORKER_BASE is not set');
    throw new Error('WORKER_BASE is not set');
  }
  return base;
}

function jsonOut_(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

/** アプリからのキック入口。 */
function doGet(e) {
  var secret = props_().getProperty('KICK_SECRET');
  if (!secret || !e || !e.parameter || e.parameter.key !== secret) {
    return jsonOut_({ ok: false, error: 'unauthorized' });
  }
  try {
    workerBase_(); // メールの有無や処理済み判定に関係なく設定漏れを通知する
    return jsonOut_(findAndPost_());
  } catch (err) {
    return jsonOut_({ ok: false, error: String(err) });
  }
}

/** 時間主導トリガー用のフォールバック (任意)。 */
function scheduledCheck() {
  workerBase_();
  findAndPost_();
}

/** メール本文 (HTML) から session_id / token を取り出す。 */
function extract_(html) {
  var m = URL_RE.exec(html || '');
  if (m) return { session_id: m[1], token: m[2] };
  var shorts = (html || '').match(SHORT_RE) || [];
  for (var i = 0; i < shorts.length; i++) {
    try {
      var resp = UrlFetchApp.fetch(shorts[i], {
        followRedirects: false,
        muteHttpExceptions: true
      });
      var headers = resp.getHeaders();
      var loc = headers['Location'] || headers['location'] || '';
      var m2 = URL_RE.exec(loc);
      if (m2) return { session_id: m2[1], token: m2[2] };
    } catch (err) {
      // 短縮 URL の解決に失敗したら次を試す
    }
  }
  return null;
}

/** Gmail を検索し、未処理の開始メールがあれば Worker へ渡す。 */
function findAndPost_() {
  var p = props_();
  var raw = p.getProperty('PROCESSED_MAIL_IDS');
  var processed = raw ? JSON.parse(raw) : [];
  if (!Array.isArray(processed) || processed.some(function(id) {
    return typeof id !== 'string' || !id;
  })) throw new Error('PROCESSED_MAIL_IDS must be a JSON array of message IDs');
  processed = processed.filter(function(id, index, ids) {
    return ids.lastIndexOf(id) === index;
  }).slice(-20);
  var lastMailId = p.getProperty('LAST_MAIL_ID');
  if (lastMailId && processed.indexOf(lastMailId) === -1) {
    processed.push(lastMailId);
    processed = processed.slice(-20);
  }
  p.setProperty('PROCESSED_MAIL_IDS', JSON.stringify(processed));

  // 検索結果をページ単位で全件確認し、未処理の最新候補を選ぶ。
  var newest = null;
  var already = false;
  var pageSize = 100;
  for (var offset = 0; ; offset += pageSize) {
    var threads = GmailApp.search('livetrack newer_than:2h', offset, pageSize);
    for (var t = 0; t < threads.length; t++) {
      var msgs = threads[t].getMessages();
      for (var i = 0; i < msgs.length; i++) {
        var msg = msgs[i];
        if (processed.indexOf(msg.getId()) !== -1) {
          already = true;
          continue;
        }
        if (newest && msg.getDate() <= newest.msg.getDate()) continue;
        var found = extract_(msg.getBody());
        if (found) newest = { msg: msg, found: found };
      }
    }
    if (threads.length < pageSize) break;
  }
  if (!newest) return { ok: true, found: false, already: already };
  if (Date.now() - newest.msg.getDate().getTime() > MAX_MAIL_AGE_MIN * 60 * 1000) {
    return { ok: true, found: false };
  }
  var mid = newest.msg.getId();

  var payload = {
    session_id: newest.found.session_id,
    token: newest.found.token,
    mail_id: mid,
    state: 'live',
    source: 'gas'
  };
  var resp = UrlFetchApp.fetch(workerBase_() + '/upload?op=session', {
    method: 'post',
    contentType: 'application/json',
    headers: { 'X-Upload-Token': p.getProperty('UPLOAD_TOKEN') },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });
  if (resp.getResponseCode() !== 200) {
    return {
      ok: false,
      found: false,
      error: 'worker rejected: ' + resp.getResponseCode() + ' ' + resp.getContentText()
    };
  }
  processed.push(mid);
  p.setProperty('PROCESSED_MAIL_IDS', JSON.stringify(processed.slice(-20)));
  p.setProperty('LAST_MAIL_ID', mid);
  p.setProperty('LAST_SESSION_ID', newest.found.session_id);
  return { ok: true, found: true, session_id: newest.found.session_id };
}

// 診断用 (エディタから手動実行する)。ログにはセッションIDだけを出す。
function debugCheck() {
  Logger.log('user: ' + Session.getEffectiveUser().getEmail());
  var threads = GmailApp.search('livetrack newer_than:2h', 0, 5);
  Logger.log('threads: ' + threads.length);
  for (var t = 0; t < threads.length; t++) {
    var msgs = threads[t].getMessages();
    for (var i = 0; i < msgs.length; i++) {
      var f = extract_(msgs[i].getBody());
      Logger.log('msg from=' + msgs[i].getFrom() +
        ' extract=' + (f ? f.session_id : 'null'));
    }
  }
  Logger.log('findAndPost_: ' + JSON.stringify(findAndPost_()));
}
