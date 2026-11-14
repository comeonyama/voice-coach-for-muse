using Toybox.Activity;
using Toybox.Communications;
using Toybox.Graphics;
using Toybox.Lang;
using Toybox.Time;
using Toybox.WatchUi;

class SendListener extends Communications.ConnectionListener {
    var owner;
    var sequence;
    function initialize(field, seq) { ConnectionListener.initialize(); owner = field; sequence = seq; }
    function onComplete() { owner.finished(sequence, true); }
    function onError() { owner.finished(sequence, false); }
}

class VcmField extends WatchUi.DataField {
    var samples = [];
    var distance = null;
    var heart = null;
    var pace = null;
    var lap = 1;
    var lapDistance = 0.0;
    var lapTime = 0;
    var seq = 0;
    var busy = false;
    var lastAttempt = null;
    var result = "--";
    var resultSeq = 0;
    // Unix seconds; -1 means no event observed in this field instance.
    var stopAt = -1;
    var resumeAt = -1;
    var stopKind = 0; // 1=stop, 2=auto-pause
    var eventCount = 0;
    var pendingEvent = false;

    function initialize() { DataField.initialize(); }
    function numberOrMissing(value) { return value == null ? -1 : value; }
    function onTimerReset() {
        samples = []; distance = null; heart = null; pace = null;
        lap = 1; lapDistance = 0.0; lapTime = 0; lastAttempt = null;
        stopAt = -1; resumeAt = -1; stopKind = 0; eventCount = 0; pendingEvent = false;
        // Do not clear busy or seq: an earlier transmission may still callback.
    }
    function onTimerLap() {
        var info = Activity.getActivityInfo();
        lap += 1;
        if (info != null) {
            if (info.elapsedDistance != null) { lapDistance = info.elapsedDistance; }
            if (info.timerTime != null) { lapTime = info.timerTime; }
        }
    }
    function onTimerStop() { recordStop(1); }
    function onTimerPause() { recordStop(2); }
    function recordStop(kind) {
        stopAt = Time.now().value(); resumeAt = -1; stopKind = kind; eventCount += 1;
        pendingEvent = true; sendEvent();
    }
    function onTimerStart() { recordResume(); }
    function onTimerResume() { recordResume(); }
    function recordResume() {
        resumeAt = Time.now().value(); eventCount += 1; pendingEvent = true; sendEvent();
    }
    function sendEvent() {
        if (!Mode.TRANSMIT || busy || !pendingEvent) { return; }
        var info = Activity.getActivityInfo();
        if (info != null && info.timerTime != null) { send(info); }
    }
    function finished(sentSeq, ok) {
        busy = false; resultSeq = sentSeq; result = ok ? "OK" : "ERR";
        if (pendingEvent) { sendEvent(); }
    }
    function compute(info) {
        if (info == null) { return; }
        distance = info.elapsedDistance;
        heart = info.currentHeartRate;
        var t = info.timerTime;
        if (info.timerState != Activity.TIMER_STATE_ON || t == null) { samples = []; pace = null; return; }
        if (distance != null) {
            if (samples.size() > 0 && (t < samples[samples.size()-1][0] || distance < samples[samples.size()-1][1])) { samples = []; }
            var kept = [];
            for (var i = 0; i < samples.size(); i += 1) {
                if (samples[i][0] >= t - 30000) { kept.add(samples[i]); }
            }
            kept.add([t, distance]);
            samples = kept;
            if (samples.size() > 1) {
                var dt = t - samples[0][0];
                var dd = distance - samples[0][1];
                pace = (dt >= 5000 && dd > 0.5) ? (dt / 1000.0 * 1000.0 / dd) : null;
            } else { pace = null; }
        } else { samples = []; pace = null; }
        if (!Mode.TRANSMIT || busy || (lastAttempt != null && t - lastAttempt < 5000 && !pendingEvent)) { return; }
        send(info);
    }
    function send(info) {
        var t = info.timerTime;
        seq += 1;
        lastAttempt = t;
        busy = true;
        pendingEvent = false;
        distance = info.elapsedDistance; heart = info.currentHeartRate;
        var payload = {"seq" => seq, "t" => t, "d" => numberOrMissing(distance),
            "sp" => numberOrMissing(info.currentSpeed), "avg" => numberOrMissing(info.averageSpeed),
            "hr" => numberOrMissing(heart), "cad" => numberOrMissing(info.currentCadence),
            "lap" => lap, "lapD" => distance == null ? -1 : distance - lapDistance,
            "alt" => numberOrMissing(info.altitude), "asc" => numberOrMissing(info.totalAscent),
            "state" => numberOrMissing(info.timerState), "stop" => stopAt, "resume" => resumeAt,
            "stopKind" => stopKind, "ev" => eventCount};
        try { Communications.transmit(payload, {}, new SendListener(self, seq)); }
        catch (e) { finished(seq, false); }
    }
    function onUpdate(dc) {
        var textColor = getBackgroundColor() == Graphics.COLOR_BLACK ? Graphics.COLOR_WHITE : Graphics.COLOR_BLACK;
        dc.setColor(textColor, Graphics.COLOR_TRANSPARENT);
        var x = dc.getWidth() / 2;
        var h = dc.getHeight();
        var d = distance == null ? "--" : (distance / 1000.0).format("%.2f");
        var hr = heart == null ? "--" : heart.format("%d");
        var p = "--:--";
        if (pace != null && pace < 6000) { var s = pace.toNumber(); p = (s / 60).format("%d") + ":" + (s % 60).format("%02d"); }
        dc.drawText(x, h / 6, Graphics.FONT_TINY, d + " km  " + hr + " bpm", Graphics.TEXT_JUSTIFY_CENTER);
        dc.drawText(x, h * 2 / 5, Graphics.FONT_SMALL, p + " /km", Graphics.TEXT_JUSTIFY_CENTER);
        dc.drawText(x, h * 2 / 3, Graphics.FONT_TINY, Mode.TRANSMIT ? result + " " + resultSeq.format("%d") : "DISPLAY ONLY", Graphics.TEXT_JUSTIFY_CENTER);
    }
}
