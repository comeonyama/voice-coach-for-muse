'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const audio = $('audio');
  const demo = new URLSearchParams(location.search).get('demo') === '1';
  const settingsKey = 'muse.receiver.settings.v1';
  const playedPrefix = 'muse.receiver.played.v1:';
  const logs = [], pending = [], known = new Set(), history = new Map(), memory = new Map();
  let running = false, current = null, pollTimer, nextTimer, controller, generation = 0;
  let wakeLock, wakeRequest = false, lastScope = null, blocked = false;
  let settings = {url: 'queue.json', interval: 5, audioMode: 'ambient'};
  const time = value => new Date(value).toLocaleTimeString('ja-JP', {hour12:false});
  function log(event, detail = '') {
    logs.push(`${new Date().toISOString()} [${event}] ${detail}`);
    if (logs.length > 2000) logs.shift();
    $('logs').textContent = logs.join('\n');
    $('logs').scrollTop = $('logs').scrollHeight;
  }
  function read(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; }
    catch (e) { log('保存領域読込エラー', e.message); return fallback; }
  }
  function write(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); }
    catch (e) { log('保存領域書込エラー', e.message); $('notice').textContent = '保存できません。再読み込み後の重複防止は利用できません。'; }
  }
  const saved = read(settingsKey, {});
  if (saved && typeof saved.url === 'string') settings.url = saved.url;
  if (saved && Number.isInteger(saved.interval) && saved.interval >= 1 && saved.interval <= 300) settings.interval = saved.interval;
  const audioModes = ['ambient', 'transient', 'transient-solo', 'auto'];
  if (saved && audioModes.includes(saved.audioMode)) settings.audioMode = saved.audioMode;
  function configureAudio() {
    if (!navigator.audioSession) {
      $('audio-status').textContent = '音楽との共存：このブラウザでは指定できません';
      log('Audio Session', '未対応 / ブラウザ標準動作');
      return;
    }
    try {
      navigator.audioSession.type = settings.audioMode;
      const actual = navigator.audioSession.type;
      $('audio-status').textContent = `音声モード：${actual}（実際の挙動は耳で確認）`;
      log('Audio Session', `要求=${settings.audioMode} / 設定値=${actual}`);
    } catch(e) {
      $('audio-status').textContent = '音声モードを指定できません。音楽が停止する場合があります';
      log('Audio Session エラー', `${e.name}: ${e.message}`);
    }
  }
  function httpURL(value, base) {
    const url = new URL(value, base);
    if (!['http:', 'https:'].includes(url.protocol)) throw new Error('HTTP / HTTPS の URL を指定してください');
    return url;
  }
  const sourceURL = () => httpURL(demo ? 'demo-queue.json' : settings.url, location.href);
  function showSettings() {
    $('audio-mode').value = settings.audioMode;
    $('queue-url').value = settings.url; $('interval').value = settings.interval;
    $('source').textContent = demo ? 'デモ中：demo-queue.json を使用（URL設定は本番用に保存）' : `受信先：${settings.url}`;
  }
  showSettings();
  if (demo) $('mode').textContent = 'デモモード';
  function played(scope) {
    if (!memory.has(scope)) { const data = read(playedPrefix + scope, []); memory.set(scope, new Set(Array.isArray(data) ? data.filter(x => typeof x === 'string') : [])); }
    return memory.get(scope);
  }
  function count() { $('pending').textContent = pending.length + (current?.message ? 1 : 0); }
  function status(item, text) { const el = history.get(item.key); if (el) el.textContent = text; }
  function addHistory(item, state) {
    if (history.has(item.key)) return;
    const li = document.createElement('li'), date = document.createElement('time'), text = document.createElement('p'), label = document.createElement('small');
    date.dateTime = item.ts; date.textContent = `${time(item.ts)} · ${item.id}`; text.textContent = item.text; label.textContent = state;
    li.append(date, text, label); $('history').prepend(li); history.set(item.key, label);
    $('empty').hidden = true; $('history-count').textContent = `${history.size} 件`;
  }
  function finish(error) {
    const job = current; if (!job) return;
    clearTimeout(job.timer); audio.onplaying = audio.onended = audio.onerror = audio.onwaiting = audio.onstalled = null;
    audio.pause(); current = null;
    const seconds = job.started == null ? '0.00' : ((performance.now() - job.started) / 1000).toFixed(2);
    if (error) { log('再生エラー', `${job.id} ${error} / 経過 ${seconds}秒`); if(job.message) status(job, 'スキップ（再生エラー）'); }
    else {
      log('再生終了', `${job.id} / ${seconds}秒`);
      if (job.message) { const ids = played(job.scope); ids.add(job.id); write(playedPrefix + job.scope, [...ids]); status(job, '再生済み'); }
    }
    $('play-state').textContent = error ? '音声エラー · テキストで確認できます' : '受信待機中';
    count(); nextTimer = setTimeout(() => { nextTimer = null; drain(); }, 300);
  }
  function begin(item) {
    clearTimeout(nextTimer); nextTimer = null;
    configureAudio();
    const job = {...item, started:null}; current = job;
    if (job.message) { $('message').textContent = job.text; status(job, '読み込み中'); }
    $('play-state').textContent = '音声を読み込み中';
    const watchdog = () => { clearTimeout(job.timer); job.timer = setTimeout(() => { if (current === job) finish('読み込み／再生停止が5秒続いたためスキップ'); }, 5000); };
    audio.onplaying = () => {
      if (current !== job) return;
      clearTimeout(job.timer);
      if (job.started == null) { job.started = performance.now(); log('再生開始', job.id); }
      $('play-state').textContent = 'コーチング再生中'; if(job.message) status(job, '再生中');
    };
    audio.onwaiting = audio.onstalled = watchdog;
    audio.onended = () => { if(current === job) finish(); };
    audio.onerror = () => { if(current === job) finish(`MediaError ${audio.error?.code ?? '?'} ${audio.error?.message ?? ''}`); };
    audio.src = job.url; watchdog(); count();
    // Must stay synchronous with start/test/resume tap for Safari user activation.
    try { const promise = audio.play(); if (promise) promise.catch(e => {
      if (current !== job) return;
      if (e.name === 'NotAllowedError') {
        blocked = true; $('resume').hidden = false;
        if(job.message) { pending.unshift(item); }
        finish(`${e.name}: ${e.message}`);
        if(job.message) status(job, 'タップで再開待ち');
        $('play-state').textContent = '音声を再開するにはタップしてください';
      } else finish(`${e.name}: ${e.message}`);
    }); } catch(e) { finish(e.message); }
  }
  function drain() { if (running && !current && !nextTimer && !blocked && pending.length) begin(pending.shift()); }
  async function poll() {
    clearTimeout(pollTimer);
    const version = generation, abort = new AbortController(); controller = abort;
    const timeout = setTimeout(() => abort.abort(), 10000);
    try {
      const base = sourceURL(), request = new URL(base); request.searchParams.set('_muse_t', Date.now());
      const response = await fetch(request, {cache:'no-store', signal:abort.signal});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (version !== generation) return;
      if (!data || typeof data.session !== 'string' || !Array.isArray(data.messages)) throw new Error('session / messages の形式が不正');
      const scope = JSON.stringify([base.href, data.session]); lastScope = scope;
      const ids = played(scope);
      const valid = [];
      for (const m of data.messages) {
        if (!m || typeof m.id !== 'string' || !m.id || typeof m.text !== 'string' || typeof m.audio !== 'string' || typeof m.ts !== 'string' || !Number.isFinite(Date.parse(m.ts))) { log('メッセージ形式エラー', 'id / ts / audio / text を確認してください'); continue; }
        valid.push(m);
      }
      valid.sort((a,b) => Date.parse(a.ts) - Date.parse(b.ts));
      for (const m of valid) {
        const key = JSON.stringify([scope,m.id]), item = {...m, scope, key, message:true};
        addHistory(item, ids.has(m.id) ? '再生済み' : '待機中');
        if (known.has(key)) continue;
        known.add(key);
        if (ids.has(m.id)) continue;
        log('新規メッセージ検出', m.id);
        if (!current?.message) $('message').textContent = m.text;
        try { item.url = httpURL(m.audio, base).href; pending.push(item); }
        catch(e) { log('再生エラー', `${m.id} ${e.message}`); status(item, 'スキップ（音声URLエラー）'); }
      }
      if (!current?.message && !pending.length && valid.length) $('message').textContent = valid[valid.length-1].text;
      $('last-poll').textContent = time(Date.now()); log('ポーリング成功', `${data.session} / ${valid.length}件`); count(); drain();
    } catch(e) { if (version === generation) log('ポーリング失敗', `${e.name}: ${e.message}`); }
    finally { clearTimeout(timeout); if (version === generation && running) {controller = null; pollTimer = setTimeout(poll, settings.interval * 1000);} }
  }
  function repoll() { generation++; controller?.abort(); clearTimeout(pollTimer); if (running) poll(); }
  async function requestWake() {
    if (!running || document.visibilityState !== 'visible' || wakeLock || wakeRequest) return;
    if (!navigator.wakeLock) { $('wake').textContent = '画面スリープ防止：この環境では未対応'; return; }
    wakeRequest = true;
    try { wakeLock = await navigator.wakeLock.request('screen'); $('wake').textContent = '画面スリープ防止：有効'; log('Wake Lock', '取得'); wakeLock.addEventListener('release', () => { wakeLock = null; $('wake').textContent = '画面スリープ防止：解除'; log('Wake Lock', '解除'); }); }
    catch(e) { $('wake').textContent = '画面スリープ防止：利用できません'; log('Wake Lock エラー', e.message); }
    finally { wakeRequest = false; }
  }
  $('start').onclick = () => {
    if (running) return;
    running = true; blocked = false; $('resume').hidden = true; $('intro').hidden = true; $('receiver').hidden = false;
    if (!current) begin({id:'開始確認音',url:new URL('demo-audio/start.mp3',location.href).href});
    requestWake(); poll();
  };
  $('test').onclick = () => {
    if (current || pending.length) { $('notice').textContent = '音声の再生・待機が終わってからテストできます。'; return; }
    $('notice').textContent = 'テスト音声を再生します。';
    begin({id:'テスト音声',url:new URL('demo-audio/tone-1.mp3',location.href).href});
  };
  $('resume').onclick = () => { blocked = false; $('resume').hidden = true; if (!current) begin(pending.length ? pending.shift() : {id:'再開確認音',url:new URL('demo-audio/start.mp3',location.href).href}); };
  $('reset').onclick = () => {
    if (current) { $('notice').textContent = '再生が終わってからリセットしてください。'; return; }
    if (lastScope) { memory.set(lastScope,new Set()); write(playedPrefix+lastScope,[]); }
    else {
      try { for (let i=localStorage.length-1;i>=0;i--) { const key=localStorage.key(i); if(key?.startsWith(playedPrefix)) localStorage.removeItem(key); } memory.clear(); }
      catch(e) { log('保存領域書込エラー', e.message); }
    }
    known.clear(); pending.length=0; history.clear(); $('history').replaceChildren(); $('history-count').textContent='0 件'; $('empty').hidden=false;
    count(); log('再生済みリセット',lastScope ?? '全セッション'); $('notice').textContent='再生済み記録を消去しました。次の受信で再生します。'; repoll();
  };
  $('settings').onsubmit = event => {
    event.preventDefault();
    try {
      const url=$('queue-url').value.trim(), interval=Number($('interval').value); httpURL(url,location.href);
      if(!url || !Number.isInteger(interval) || interval<1 || interval>300) throw new Error('間隔は1〜300秒の整数で指定してください');
      if(current || pending.length) throw new Error('再生・待機が終わってから設定を変更してください');
      const audioMode = $('audio-mode').value;
      if (!audioModes.includes(audioMode)) throw new Error('音声モードが不正です');
      settings={url,interval,audioMode}; configureAudio(); write(settingsKey,settings); showSettings(); $('notice').textContent='設定を保存しました。'; repoll();
    } catch(e) { $('notice').textContent=e.message; }
  };
  $('copy').onclick = async () => {
    try { await navigator.clipboard.writeText(logs.join('\n')); $('notice').textContent='ログをコピーしました。'; }
    catch(e) { const field=$('copy-fallback'); field.hidden=false; field.value=logs.join('\n'); field.focus(); field.select(); $('notice').textContent='ログを選択しました。長押しまたはコピー操作でコピーしてください。'; }
  };
  document.addEventListener('visibilitychange', () => { log('表示状態',document.visibilityState); if(document.visibilityState==='visible') { requestWake(); if(running && !controller) repoll(); } });
  window.addEventListener('pageshow', () => { requestWake(); });
  log('ページ準備完了', `v1.1 / ${demo ? 'デモモード' : '通常モード'}`);
  configureAudio();
  if (navigator.audioSession?.addEventListener) navigator.audioSession.addEventListener('statechange', () => log('Audio Session 状態', navigator.audioSession.state));
})();
