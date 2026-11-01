<?php
// Standalone receiver: no WordPress bootstrap, no external dependencies.
declare(strict_types=1);
ini_set('display_errors', '0');
const UPLOAD_TOKEN_SHA256 = '7ea9fdbb41d15cc9be34f2adfbaeb20242d448c08b5a6b663ce58d3c6aba3f1a';
const AUDIO_MAX_BYTES = 2 * 1024 * 1024;
header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function reply(int $status, string $error = ''): void {
    http_response_code($status);
    echo json_encode($status === 200 ? ['ok' => true] : ['ok' => false, 'error' => $error], JSON_UNESCAPED_UNICODE);
    exit;
}
if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    header('Allow: POST');
    reply(405, 'POST required');
}
$token = $_SERVER['HTTP_X_UPLOAD_TOKEN'] ?? '';
if (!is_string($token) || $token === '' || !hash_equals(UPLOAD_TOKEN_SHA256, hash('sha256', $token))) {
    reply(401, 'Unauthorized');
}
unset($token);

$base = __DIR__;
$op = $_GET['op'] ?? '';
// All authenticated mutations share a lock on this script (no extra lock file).
// Atomic queue replacement alone cannot prevent cleanup racing a queue update.
$operationLock = @fopen(__FILE__, 'rb');
if ($operationLock === false || !flock($operationLock, LOCK_EX)) {
    reply(500, 'Cannot lock operation');
}
register_shutdown_function(static function () use ($operationLock): void {
    flock($operationLock, LOCK_UN);
    fclose($operationLock);
});
if ($op === 'cleanup') {
    $body = file_get_contents('php://input');
    if ($body === false) { reply(400, 'Cannot read body'); }
    $minAge = 3600;
    if (trim($body) !== '') {
        try { $options = json_decode($body, false, 512, JSON_THROW_ON_ERROR); }
        catch (JsonException $e) { reply(400, 'Invalid JSON'); }
        if (!is_object($options)) { reply(400, 'Expected JSON object'); }
        if (property_exists($options, 'min_age_sec')) {
            if (!is_int($options->min_age_sec) || $options->min_age_sec < 0) {
                reply(400, 'min_age_sec must be a nonnegative integer');
            }
            $minAge = $options->min_age_sec;
        }
    }
    $queuePath = $base . '/queue.json';
    if (is_link($queuePath) || !is_file($queuePath)) { reply(500, 'Queue unavailable'); }
    $raw = @file_get_contents($queuePath);
    if ($raw === false) { reply(500, 'Queue unreadable'); }
    try { $queue = json_decode($raw, false, 512, JSON_THROW_ON_ERROR); }
    catch (JsonException $e) { reply(500, 'Invalid queue JSON'); }
    if (!is_object($queue) || !isset($queue->session) || !is_string($queue->session)
        || !isset($queue->messages) || !is_array($queue->messages)) {
        reply(500, 'Invalid queue structure');
    }
    $referenced = [];
    foreach ($queue->messages as $message) {
        if (!is_object($message) || !isset($message->audio) || !is_string($message->audio) || $message->audio === '') {
            reply(500, 'Invalid queue audio reference');
        }
        $path = parse_url($message->audio, PHP_URL_PATH);
        if (!is_string($path) || $path === '') { reply(500, 'Invalid queue audio reference'); }
        // Conservatively protect matching basenames for relative and absolute URLs.
        $referenced[basename(rawurldecode($path))] = true;
    }
    $directory = $base . '/audio';
    if (is_link($directory) || realpath($directory) !== $directory || !is_dir($directory)) {
        reply(500, 'Audio directory unavailable');
    }
    $names = @scandir($directory);
    if ($names === false) { reply(500, 'Cannot list audio directory'); }
    $now = time();
    $deleted = [];
    foreach ($names as $name) {
        if (preg_match('/\A[a-z0-9][a-z0-9-]*\.mp3\z/D', $name) !== 1 || isset($referenced[$name])) { continue; }
        $path = $directory . '/' . $name;
        clearstatcache(true, $path);
        $stat = @lstat($path);
        // lstat checks the entry itself. Never follow symlinks or remove directories.
        if ($stat === false || ($stat['mode'] & 0170000) !== 0100000) { continue; }
        if ($stat['mtime'] > $now || $now - $stat['mtime'] < $minAge) { continue; }
        if (!@unlink($path)) { reply(500, 'Cannot delete audio file'); }
        $deleted[] = $name;
    }
    http_response_code(200);
    echo json_encode(['ok' => true, 'deleted' => $deleted, 'count' => count($deleted)], JSON_UNESCAPED_UNICODE);
    exit;
}
if ($op === 'audio') {
    $name = $_GET['name'] ?? '';
    if (!is_string($name) || strlen($name) > 255 || preg_match('/\A[a-z0-9][a-z0-9-]*\.mp3\z/D', $name) !== 1) {
        reply(400, 'Invalid audio filename');
    }
    $directory = $base . '/audio';
    // Reject a redirected/symlinked audio directory; never write outside this tree.
    if (is_link($directory) || realpath($directory) !== $directory || !is_dir($directory)) {
        reply(500, 'Audio directory unavailable');
    }
    $destination = $directory . '/' . $name;
    if (isset($_SERVER['CONTENT_LENGTH']) && (float)$_SERVER['CONTENT_LENGTH'] > AUDIO_MAX_BYTES) {
        reply(413, 'Audio exceeds 2 MiB');
    }
    $input = @fopen('php://input', 'rb');
    if ($input === false) { reply(400, 'Cannot read body'); }
    $body = stream_get_contents($input, AUDIO_MAX_BYTES + 1);
    fclose($input);
    if ($body === false) { reply(400, 'Cannot read body'); }
    if (strlen($body) > AUDIO_MAX_BYTES) { reply(413, 'Audio exceeds 2 MiB'); }
    if ($body === '') { reply(400, 'Empty audio body'); }
} elseif ($op === 'queue') {
    $directory = $base;
    $destination = $base . '/queue.json';
    $body = file_get_contents('php://input');
    if ($body === false) { reply(400, 'Cannot read body'); }
    try {
        $queue = json_decode($body, false, 512, JSON_THROW_ON_ERROR);
    } catch (JsonException $e) { reply(400, 'Invalid JSON'); }
    if (!is_object($queue) || !isset($queue->session) || !is_string($queue->session)
        || !isset($queue->messages) || !is_array($queue->messages)) {
        reply(400, 'session must be a string and messages must be an array');
    }
} else {
    reply(400, 'Invalid operation');
}
if (is_link($destination) || (file_exists($destination) && !is_file($destination))) {
    reply(400, 'Invalid destination');
}
// Exclusive creation in the target directory: no tempnam fallback to /tmp.
$temp = $directory . '/.upload-' . bin2hex(random_bytes(16)) . '.tmp';
$handle = @fopen($temp, 'x+b');
if ($handle === false) { reply(500, 'Cannot create temporary file'); }
$success = false;
try {
    $offset = 0;
    $length = strlen($body);
    while ($offset < $length) {
        $written = @fwrite($handle, substr($body, $offset));
        if ($written === false || $written === 0) { throw new RuntimeException('write'); }
        $offset += $written;
    }
    if (!fflush($handle)) { throw new RuntimeException('flush'); }
    if (!@chmod($temp, 0644)) { throw new RuntimeException('permissions'); }
    fclose($handle);
    $handle = null;
    if (!@rename($temp, $destination)) { throw new RuntimeException('rename'); }
    $success = true;
} catch (Throwable $e) {
    // Do not log request headers, body or credentials.
} finally {
    if (is_resource($handle)) { fclose($handle); }
    if (!$success) { @unlink($temp); }
}
if (!$success) { reply(500, 'Cannot save upload'); }
reply(200);
