#import <UIKit/UIKit.h>
#import <ConnectIQ/ConnectIQ.h>

#import <AVFoundation/AVFoundation.h>
#import <Security/Security.h>

static NSString *const kLiveService = @"vcm.iq.poc.upload";
static NSString *ISO(NSDate *date) {
    NSISO8601DateFormatter *f = [NSISO8601DateFormatter new];
    f.formatOptions = NSISO8601DateFormatWithInternetDateTime | NSISO8601DateFormatWithFractionalSeconds;
    return [f stringFromDate:date];
}
static NSDictionary *KeyQuery(void) { return @{(__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword, (__bridge id)kSecAttrService:kLiveService, (__bridge id)kSecAttrAccount:@"worker"}; }
static NSString *ReadToken(void) {
    NSMutableDictionary *q = [KeyQuery() mutableCopy]; q[(__bridge id)kSecReturnData] = @YES; q[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
    CFTypeRef found = NULL; if (SecItemCopyMatching((__bridge CFDictionaryRef)q, &found) != errSecSuccess) return @"";
    NSData *data = CFBridgingRelease(found); return [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] ?: @"";
}
static BOOL SaveToken(NSString *token) {
    NSDictionary *q = KeyQuery(); NSData *data = [token dataUsingEncoding:NSUTF8StringEncoding];
    if (!token.length) { OSStatus s = SecItemDelete((__bridge CFDictionaryRef)q); return s == errSecSuccess || s == errSecItemNotFound; }
    OSStatus s = SecItemUpdate((__bridge CFDictionaryRef)q, (__bridge CFDictionaryRef)@{(__bridge id)kSecValueData:data});
    if (s == errSecItemNotFound) { NSMutableDictionary *item = [q mutableCopy]; item[(__bridge id)kSecValueData] = data; item[(__bridge id)kSecAttrAccessible] = (__bridge id)kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly; s = SecItemAdd((__bridge CFDictionaryRef)item, NULL); }
    return s == errSecSuccess;
}

@interface LiveBridge : NSObject <NSURLSessionTaskDelegate, AVSpeechSynthesizerDelegate>
@property(nonatomic,strong) NSMutableDictionary *saved;
@property(nonatomic,strong) NSDictionary *message;
@property(nonatomic,strong) NSMutableArray<NSArray<NSNumber *> *> *samples;
@property(nonatomic,strong) NSDate *arrival;
@property(nonatomic,strong) NSTimer *timer;
@property(nonatomic,strong) AVSpeechSynthesizer *speaker;
@property(nonatomic,strong) NSURLSession *network;
@property(nonatomic,assign) BOOL inflight;
@property(nonatomic,assign) BOOL pending;
@property(nonatomic,assign) BOOL closed;
@property(nonatomic,assign) NSInteger attempts;
@property(nonatomic,assign) NSInteger successes;
@property(nonatomic,assign) double maxGap;
@property(nonatomic,assign) double recent;
@property(nonatomic,assign) NSInteger lastSpeechBucket;
@property(nonatomic,copy) void (^changed)(void);
- (void)accept:(NSDictionary *)m arrival:(NSDate *)arrival;
- (void)send;
- (void)end;
- (void)begin;
@end
@implementation LiveBridge
- (instancetype)init {
    if ((self = [super init])) {
        _saved = [[NSUserDefaults.standardUserDefaults dictionaryForKey:@"liveSession"] mutableCopy] ?: [NSMutableDictionary dictionary];
        _closed = [_saved[@"closed"] boolValue]; _message = _saved[@"message"]; _arrival = _saved[@"arrival"] ? [[[NSISO8601DateFormatter alloc] init] dateFromString:_saved[@"arrival"]] : nil; _samples = [NSMutableArray array]; _speaker = [AVSpeechSynthesizer new]; _speaker.delegate = self;
        NSURLSessionConfiguration *c = NSURLSessionConfiguration.ephemeralSessionConfiguration; c.timeoutIntervalForRequest = 12; c.timeoutIntervalForResource = 20;
        _network = [NSURLSession sessionWithConfiguration:c delegate:self delegateQueue:nil];
        _timer = [NSTimer scheduledTimerWithTimeInterval:15 target:self selector:@selector(tick:) userInfo:nil repeats:YES];
    } return self;
}
- (void)persist { self.saved[@"closed"] = @(self.closed); [NSUserDefaults.standardUserDefaults setObject:self.saved forKey:@"liveSession"]; }
- (void)begin { self.closed = NO; self.message = nil; self.arrival = nil; self.recent = 0; self.lastSpeechBucket = 0; [self.samples removeAllObjects]; [self.saved removeAllObjects]; [self persist]; if (self.changed) self.changed(); }
- (void)end { if (!self.saved[@"id"] || self.closed) return; self.closed = YES; [self persist]; [self send]; if (self.changed) self.changed(); }
- (NSString *)zone:(NSInteger)hr { if (hr <= 0) return nil; return hr <= 120 ? @"余裕あり" : hr <= 135 ? @"適正" : hr <= 150 ? @"少し高め" : @"高め"; }
- (void)accept:(NSDictionary *)m arrival:(NSDate *)arrival {
    if (self.closed) return;
    NSInteger seq = [m[@"seq"] integerValue]; double t = [m[@"t"] doubleValue], d = [m[@"d"] doubleValue];
    BOOL fresh = !self.saved[@"id"];
    // A timer reset is evidence of a new activity; sequence alone can reset on watch app restart.
    if (!fresh && self.saved[@"lastT"] && t + 1000 < [self.saved[@"lastT"] doubleValue]) { [self begin]; fresh = YES; }
    if (fresh) { self.saved[@"id"] = NSUUID.UUID.UUIDString; self.saved[@"count"] = @0; self.saved[@"gaps"] = @0; }
    NSInteger priorEv = [self.saved[@"lastEv"] integerValue]; NSInteger priorSeq = [self.saved[@"lastSeq"] integerValue]; NSInteger priorState = self.saved[@"lastState"] ? [self.saved[@"lastState"] integerValue] : -1;
    NSInteger state = [m[@"state"] isKindOfClass:NSNumber.class] ? [m[@"state"] integerValue] : -1;
    NSInteger previousLap = [self.saved[@"lastLap"] integerValue]; NSInteger lap = [m[@"lap"] integerValue];
    BOOL staleGap = self.arrival && [arrival timeIntervalSinceDate:self.arrival] > 15;
    if (self.arrival) self.maxGap = MAX(self.maxGap, [arrival timeIntervalSinceDate:self.arrival]);
    BOOL firstAfterLaunch = self.message == nil || self.arrival == nil; self.message = m; self.arrival = arrival;
    self.saved[@"count"] = @([self.saved[@"count"] integerValue] + 1);
    if (priorSeq > 0 && seq > priorSeq + 1) self.saved[@"gaps"] = @([self.saved[@"gaps"] integerValue] + seq-priorSeq-1);
    NSMutableDictionary *safe = [NSMutableDictionary dictionary]; for (NSString *k in @[@"seq",@"t",@"d",@"sp",@"avg",@"hr",@"cad",@"lap",@"lapD",@"alt",@"asc",@"state",@"stop",@"resume",@"stopKind",@"ev"]) if ([m[k] isKindOfClass:NSNumber.class]) safe[k] = m[k]; self.saved[@"message"] = safe;
    if ([m[@"ev"] isKindOfClass:NSNumber.class]) self.saved[@"lastEv"] = m[@"ev"];
    self.saved[@"lastSeq"] = @(seq); self.saved[@"lastT"] = @(t); self.saved[@"lastState"] = @(state); self.saved[@"lastLap"] = @(lap); self.saved[@"arrival"] = ISO(arrival);
    if (state != 3 || priorState != 3 || staleGap || d < 0 || (self.samples.count && (t < self.samples.lastObject[0].doubleValue || d < self.samples.lastObject[1].doubleValue))) { [self.samples removeAllObjects]; self.recent = 0; }
    if (state == 3 && t >= 0 && d >= 0) {
        [self.samples addObject:@[@(t), @(d)]];
        while (self.samples.count > 1 && self.samples[1][0].doubleValue <= t - 30000) [self.samples removeObjectAtIndex:0];
        NSArray *first = self.samples.firstObject; double firstT = [first[0] doubleValue], firstD = [first[1] doubleValue];
        if (self.samples.count > 1 && firstT < t - 30000) { NSArray *next = self.samples[1]; double span = [next[0] doubleValue] - firstT; if (span > 0) { firstD += ([next[1] doubleValue]-firstD) * (t-30000-firstT)/span; firstT = t-30000; } }
        double dt = (t-firstT)/1000.0, dd = d-firstD;
        self.recent = dt >= 10 && dd > 0.5 ? dt*1000/dd : 0;
    }
    [self persist];
    NSString *mode = [NSUserDefaults.standardUserDefaults stringForKey:@"paceMode"] ?: @"lap";
    NSInteger bucket = [mode isEqualToString:@"60"] ? (NSInteger)(t/60000) : [mode isEqualToString:@"180"] ? (NSInteger)(t/180000) : 0;
    if (fresh || firstAfterLaunch) self.lastSpeechBucket = bucket;
    BOOL speak = !fresh && !firstAfterLaunch && state == 3 && (([mode isEqualToString:@"lap"] && previousLap > 0 && lap > previousLap) || (bucket > 0 && bucket > self.lastSpeechBucket));
    if (bucket > self.lastSpeechBucket) self.lastSpeechBucket = bucket;
    if (speak) [self speak];
    if (fresh || (priorState != -1 && state != priorState) || ([m[@"ev"] isKindOfClass:NSNumber.class] && [m[@"ev"] integerValue] != priorEv)) [self send];
    if (self.changed) self.changed();
}
- (NSDictionary *)snapshot {
    NSMutableDictionary *latest = [NSMutableDictionary dictionary];
    for (NSString *k in @[@"seq",@"t",@"d",@"sp",@"avg",@"hr",@"cad",@"lap",@"lapD",@"alt",@"asc",@"state",@"stop",@"resume",@"stopKind",@"ev"]) latest[k] = [self.message[k] isKindOfClass:NSNumber.class] ? self.message[k] : NSNull.null;
    double t = [self.message[@"t"] doubleValue], d = [self.message[@"d"] doubleValue]; NSInteger state = [self.message[@"state"] integerValue];
    NSString *zone = [self zone:[self.message[@"hr"] integerValue]];
    NSMutableArray *laps = [NSMutableArray array];
    // The payload lacks completed-lap boundary time/distance, so never invent completed laps or pace.
    if ([self.message[@"lap"] integerValue] > 0 && [self.message[@"lapD"] isKindOfClass:NSNumber.class] && [self.message[@"lapD"] doubleValue] >= 0) [laps addObject:@{@"lap":self.message[@"lap"],@"distanceM":self.message[@"lapD"],@"paceSecPerKm":NSNull.null,@"status":@"current"}];
    return @{@"version":@1,@"sessionId":self.saved[@"id"],@"sentAt":ISO(NSDate.date),@"timerState":state == 3 ? @"on" : (state == 1 || state == 2) ? @"paused" : @"unknown",@"closedByReceiver":@(self.closed),@"latest":latest,@"derived":@{@"overallPaceSecPerKm":t >= 0 && d > 0 ? @(t/d) : NSNull.null,@"recentPaceSecPerKm":self.recent > 0 ? @(self.recent) : NSNull.null,@"hrZone":zone ?: (id)NSNull.null},@"laps":laps,@"stats":@{@"receivedCount":self.saved[@"count"] ?: @0,@"gapTotal":self.saved[@"gaps"] ?: @0,@"lastSeq":self.saved[@"lastSeq"] ?: NSNull.null,@"lastReceivedAt":self.saved[@"arrival"] ?: NSNull.null}};
}
- (void)tick:(NSTimer *)timer { if (!self.closed && self.message) [self send]; }
- (void)send {
    if (!self.message || !self.saved[@"id"]) return;
    NSString *raw = [NSUserDefaults.standardUserDefaults stringForKey:@"liveEndpoint"] ?: @""; NSString *token = ReadToken();
    NSURLComponents *c = [NSURLComponents componentsWithString:raw];
    if (![c.scheme.lowercaseString isEqualToString:@"https"] || !c.host.length || c.user || c.password || !token.length) return;
    c.path = [c.path stringByTrimmingCharactersInSet:[NSCharacterSet characterSetWithCharactersInString:@"/"]] ; c.path = [@"/" stringByAppendingString:c.path]; c.path = [c.path isEqualToString:@"/"] ? @"/upload" : [c.path stringByAppendingString:@"/upload"]; c.queryItems = @[[NSURLQueryItem queryItemWithName:@"op" value:@"live"]]; c.fragment = nil;
    if (self.inflight) { self.pending = YES; return; }
    NSData *body = [NSJSONSerialization dataWithJSONObject:[self snapshot] options:0 error:nil]; if (!body || !c.URL) return;
    NSMutableURLRequest *req = [NSMutableURLRequest requestWithURL:c.URL]; req.HTTPMethod = @"POST"; req.HTTPBody = body; [req setValue:@"application/json" forHTTPHeaderField:@"Content-Type"]; [req setValue:token forHTTPHeaderField:@"X-Upload-Token"];
    self.inflight = YES; self.attempts++; if (self.changed) self.changed();
    [[self.network dataTaskWithRequest:req completionHandler:^(NSData *data, NSURLResponse *response, NSError *error) { dispatch_async(dispatch_get_main_queue(), ^{ self.inflight = NO; if (!error && [(NSHTTPURLResponse *)response statusCode] == 200) self.successes++; BOOL pending = self.pending; self.pending = NO; if (self.changed) self.changed(); if (pending) [self send]; }); }] resume];
}
- (void)URLSession:(NSURLSession *)session task:(NSURLSessionTask *)task willPerformHTTPRedirection:(NSHTTPURLResponse *)response newRequest:(NSURLRequest *)request completionHandler:(void (^)(NSURLRequest *))completionHandler { completionHandler(nil); }
- (NSString *)duration:(double)seconds { NSInteger n = (NSInteger)round(seconds); return [NSString stringWithFormat:@"%ld分%ld秒",(long)(n/60),(long)(n%60)]; }
- (void)speak {
    if (!self.arrival || -[self.arrival timeIntervalSinceNow] > 15 || [self.message[@"state"] integerValue] != 3 || self.speaker.isSpeaking) return;
    NSMutableString *text = [NSMutableString stringWithFormat:@"経過時間、%@。",[self duration:[self.message[@"t"] doubleValue]/1000]];
    if (self.recent > 0) [text appendFormat:@"直近ペース、%@。",[self duration:self.recent]];
    double d = [self.message[@"d"] doubleValue], t = [self.message[@"t"] doubleValue]; if (d > 0 && t >= 0) [text appendFormat:@"全体ペース、%@。",[self duration:t/d]];
    NSString *zone = [self zone:[self.message[@"hr"] integerValue]]; if (zone) [text appendFormat:@"心拍は%@です。",zone];
    NSError *error = nil; AVAudioSession *audio = AVAudioSession.sharedInstance;
    if (![audio setCategory:AVAudioSessionCategoryPlayback mode:AVAudioSessionModeSpokenAudio options:AVAudioSessionCategoryOptionDuckOthers error:&error] || ![audio setActive:YES error:&error]) return;
    AVSpeechUtterance *u = [AVSpeechUtterance speechUtteranceWithString:text]; u.voice = [AVSpeechSynthesisVoice voiceWithLanguage:@"ja-JP"]; u.volume = 1.0; [self.speaker speakUtterance:u];
}
- (void)speechSynthesizer:(AVSpeechSynthesizer *)s didFinishSpeechUtterance:(AVSpeechUtterance *)u { [AVAudioSession.sharedInstance setActive:NO withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:nil]; }
- (void)speechSynthesizer:(AVSpeechSynthesizer *)s didCancelSpeechUtterance:(AVSpeechUtterance *)u { [AVAudioSession.sharedInstance setActive:NO withOptions:AVAudioSessionSetActiveOptionNotifyOthersOnDeactivation error:nil]; }
@end

static NSString *const kScheme = @"vcm-iq-poc-7ac29246";
static NSString *const kAppUUID = @"7ac29246-e724-4d45-a145-41d19c543680";

@interface Receiver : UIViewController <IQDeviceEventDelegate, IQAppMessageDelegate>
@property(nonatomic, strong) LiveBridge *bridge;
@property(nonatomic, strong) UILabel *transport;
@property(nonatomic, strong) UILabel *status;
@property(nonatomic, strong) UILabel *latest;
@property(nonatomic, strong) IQDevice *device;
@property(nonatomic, strong) IQApp *watchApp;
@property(nonatomic, strong) NSURL *logURL;
@end
@implementation Receiver
- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = UIColor.systemBackgroundColor;
    self.title = @"VCM Connect IQ PoC";
    self.status = [UILabel new]; self.status.numberOfLines = 0; self.status.text = @"デバイスを選択してください";
    self.latest = [UILabel new]; self.latest.numberOfLines = 0; self.latest.text = @"受信待ち";
    UIButton *choose = [UIButton buttonWithType:UIButtonTypeSystem];
    [choose setTitle:@"Garmin デバイスを選択" forState:UIControlStateNormal];
    [choose addTarget:self action:@selector(choose) forControlEvents:UIControlEventTouchUpInside];
    UIButton *share = [UIButton buttonWithType:UIButtonTypeSystem];
    [share setTitle:@"ログを共有" forState:UIControlStateNormal];
    [share addTarget:self action:@selector(share:) forControlEvents:UIControlEventTouchUpInside];
    UIButton *settings = [UIButton buttonWithType:UIButtonTypeSystem]; [settings setTitle:@"Worker・読み上げ設定" forState:UIControlStateNormal]; [settings addTarget:self action:@selector(settings) forControlEvents:UIControlEventTouchUpInside];
    UIButton *end = [UIButton buttonWithType:UIButtonTypeSystem]; [end setTitle:@"受信を終了" forState:UIControlStateNormal]; [end addTarget:self action:@selector(endLive) forControlEvents:UIControlEventTouchUpInside];
    UIButton *begin = [UIButton buttonWithType:UIButtonTypeSystem]; [begin setTitle:@"新しい受信を開始" forState:UIControlStateNormal]; [begin addTarget:self action:@selector(beginLive) forControlEvents:UIControlEventTouchUpInside];
    self.transport = [UILabel new]; self.transport.numberOfLines = 0;
    self.bridge = [LiveBridge new]; __weak Receiver *weakSelf = self;
    self.bridge.changed = ^{ Receiver *r = weakSelf; r.transport.text = [NSString stringWithFormat:@"送信 成功 %ld / 試行 %ld · 最大受信間隔 %.1f秒%@", (long)r.bridge.successes, (long)r.bridge.attempts, r.bridge.maxGap, r.bridge.closed ? @" · 終了" : @""]; }; self.bridge.changed();
    UIStackView *stack = [[UIStackView alloc] initWithArrangedSubviews:@[choose, self.status, self.latest, self.transport, settings, end, begin, share]];
    stack.axis = UILayoutConstraintAxisVertical; stack.spacing = 20; stack.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[[stack.leadingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.leadingAnchor constant:20], [stack.trailingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.trailingAnchor constant:-20], [stack.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor constant:24]]];
    NSString *name = [NSString stringWithFormat:@"vcm-%@.jsonl", NSUUID.UUID.UUIDString];
    self.logURL = [[NSFileManager.defaultManager URLsForDirectory:NSDocumentDirectory inDomains:NSUserDomainMask].firstObject URLByAppendingPathComponent:name];
    IQDevice *saved = [self restoreDevice];
    if (saved) [self useDevice:saved];
}
- (void)settings {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:@"Worker 設定" message:@"HTTPS のエンドポイントとアップロードトークン。未設定なら送信しません。" preferredStyle:UIAlertControllerStyleAlert];
    [a addTextFieldWithConfigurationHandler:^(UITextField *f){ f.placeholder = @"https://..."; f.text = [NSUserDefaults.standardUserDefaults stringForKey:@"liveEndpoint"]; f.keyboardType = UIKeyboardTypeURL; f.autocapitalizationType = UITextAutocapitalizationTypeNone; }];
    [a addTextFieldWithConfigurationHandler:^(UITextField *f){ f.placeholder = @"アップロードトークン（変更時のみ入力）"; f.secureTextEntry = YES; f.autocapitalizationType = UITextAutocapitalizationTypeNone; }];
    [a addAction:[UIAlertAction actionWithTitle:@"保存" style:UIAlertActionStyleDefault handler:^(UIAlertAction *x){ NSString *token = a.textFields[1].text; if (token.length && !SaveToken(token)) { self.status.text = @"トークンを保存できませんでした"; return; } [NSUserDefaults.standardUserDefaults setObject:a.textFields[0].text ?: @"" forKey:@"liveEndpoint"]; [self paceSettings]; }]];
    [a addAction:[UIAlertAction actionWithTitle:@"キャンセル" style:UIAlertActionStyleCancel handler:nil]]; [self presentViewController:a animated:YES completion:nil];
}
- (void)paceSettings {
    UIAlertController *a = [UIAlertController alertControllerWithTitle:@"ペース読み上げ" message:@"読み上げ専用の間隔" preferredStyle:UIAlertControllerStyleActionSheet];
    NSArray *labels = @[@"ラップごと（既定）",@"3分ごと",@"1分ごと",@"オフ"]; NSArray *values = @[@"lap",@"180",@"60",@"off"];
    for (NSUInteger i=0; i<labels.count; i++) [a addAction:[UIAlertAction actionWithTitle:labels[i] style:UIAlertActionStyleDefault handler:^(UIAlertAction *x){ [NSUserDefaults.standardUserDefaults setObject:values[i] forKey:@"paceMode"]; NSInteger interval = [values[i] integerValue]; self.bridge.lastSpeechBucket = interval > 0 ? [self.bridge.message[@"t"] integerValue] / (interval * 1000) : 0; }]];
    [a addAction:[UIAlertAction actionWithTitle:@"キャンセル" style:UIAlertActionStyleCancel handler:nil]]; a.popoverPresentationController.sourceView = self.view; [self presentViewController:a animated:YES completion:nil];
}
- (void)endLive { [self.bridge end]; }
- (void)beginLive { [self.bridge begin]; }
- (IQDevice *)restoreDevice {
    NSData *data = [NSUserDefaults.standardUserDefaults dataForKey:@"device"];
    if (!data) return nil;
    NSError *error = nil;
    IQDevice *device = [NSKeyedUnarchiver unarchivedObjectOfClass:IQDevice.class fromData:data error:&error];
    if (error) self.status.text = [@"デバイスの復元に失敗: " stringByAppendingString:error.localizedDescription];
    return device;
}
- (void)choose { [[ConnectIQ sharedInstance] showConnectIQDeviceSelection]; }
- (void)handleURL:(NSURL *)url {
    if (![url.scheme isEqualToString:kScheme]) return;
    NSArray *devices = [[ConnectIQ sharedInstance] parseDeviceSelectionResponseFromURL:url];
    IQDevice *device = [devices.firstObject isKindOfClass:IQDevice.class] ? devices.firstObject : nil;
    if (!device) { self.status.text = @"デバイスが選択されませんでした"; return; }
    NSError *error = nil;
    NSData *data = [NSKeyedArchiver archivedDataWithRootObject:device requiringSecureCoding:YES error:&error];
    if (data) [NSUserDefaults.standardUserDefaults setObject:data forKey:@"device"];
    [self useDevice:device];
    if (error) self.status.text = [@"デバイスの保存に失敗: " stringByAppendingString:error.localizedDescription];
}
- (void)useDevice:(IQDevice *)device {
    ConnectIQ *sdk = [ConnectIQ sharedInstance];
    if (self.device) [sdk unregisterForDeviceEvents:self.device delegate:self];
    if (self.watchApp) [sdk unregisterForAppMessages:self.watchApp delegate:self];
    self.device = device;
    NSUUID *uuid = [[NSUUID alloc] initWithUUIDString:kAppUUID];
    self.watchApp = [IQApp appWithUUID:uuid storeUuid:uuid device:device];
    [sdk registerForDeviceEvents:device delegate:self];
    [sdk registerForAppMessages:self.watchApp delegate:self];
    self.status.text = [NSString stringWithFormat:@"接続待ち: %@", device.friendlyName];
}
- (void)deviceStatusChanged:(IQDevice *)device status:(IQDeviceStatus)status {
    dispatch_async(dispatch_get_main_queue(), ^{ self.status.text = [NSString stringWithFormat:@"%@ 接続状態: %ld", device.friendlyName, (long)status]; });
}
- (void)deviceCharacteristicsDiscovered:(IQDevice *)device {
    dispatch_async(dispatch_get_main_queue(), ^{ self.status.text = [NSString stringWithFormat:@"%@ 通信準備完了", device.friendlyName]; });
}
- (void)receivedMessage:(id)message fromApp:(IQApp *)app {
    NSDate *arrival = NSDate.date;
    dispatch_async(dispatch_get_main_queue(), ^{
        if (![message isKindOfClass:NSDictionary.class]) { self.status.text = @"不正な受信形式"; return; }
        if (self.bridge.closed) return;
        NSDictionary *m = message;
        if (![m[@"seq"] isKindOfClass:NSNumber.class] || ![m[@"t"] isKindOfClass:NSNumber.class] || ![m[@"d"] isKindOfClass:NSNumber.class] || ![m[@"hr"] isKindOfClass:NSNumber.class]) { self.status.text = @"必要な数値が不足"; return; }
        NSISO8601DateFormatter *formatter = [NSISO8601DateFormatter new];
        formatter.formatOptions = NSISO8601DateFormatWithInternetDateTime | NSISO8601DateFormatWithFractionalSeconds;
        NSMutableDictionary *row = [@{@"arrival": [formatter stringFromDate:arrival], @"seq":m[@"seq"], @"t":m[@"t"], @"d":m[@"d"], @"hr":m[@"hr"]} mutableCopy];
        for (NSString *key in @[@"state", @"stop", @"resume", @"stopKind", @"ev"]) {
            if ([m[key] isKindOfClass:NSNumber.class]) row[key] = m[key];
        }
        NSError *error = nil;
        NSData *json = [NSJSONSerialization dataWithJSONObject:row options:0 error:&error];
        if (!json) { self.status.text = [@"ログ変換エラー: " stringByAppendingString:error.localizedDescription]; return; }
        if (![NSFileManager.defaultManager fileExistsAtPath:self.logURL.path]) [NSFileManager.defaultManager createFileAtPath:self.logURL.path contents:nil attributes:nil];
        NSFileHandle *handle = [NSFileHandle fileHandleForWritingToURL:self.logURL error:&error];
        if (handle) {
            @try { [handle seekToEndOfFile]; [handle writeData:json]; [handle writeData:[@"\n" dataUsingEncoding:NSUTF8StringEncoding]]; [handle closeFile]; }
            @catch (NSException *exception) { self.status.text = [@"ログ書込エラー: " stringByAppendingString:exception.reason ?: @""]; return; }
        } else { self.status.text = [@"ログ書込エラー: " stringByAppendingString:error.localizedDescription ?: @""]; return; }
        [self.bridge accept:m arrival:arrival];
        self.latest.text = [NSString stringWithFormat:@"受信: %@\nseq: %@  timer(ms): %@\n距離(m): %@  心拍: %@\n全データ: %@", row[@"arrival"], m[@"seq"], m[@"t"], m[@"d"], m[@"hr"], m];
    });
}
- (void)share:(UIButton *)sender {
    if (![NSFileManager.defaultManager fileExistsAtPath:self.logURL.path]) { self.status.text = @"共有できるログはまだありません"; return; }
    UIActivityViewController *vc = [[UIActivityViewController alloc] initWithActivityItems:@[self.logURL] applicationActivities:nil];
    vc.popoverPresentationController.sourceView = sender;
    [self presentViewController:vc animated:YES completion:nil];
}
@end

@interface AppDelegate : UIResponder <UIApplicationDelegate>
@end
@implementation AppDelegate
- (BOOL)application:(UIApplication *)app didFinishLaunchingWithOptions:(NSDictionary *)options {
    [[ConnectIQ sharedInstance] initializeWithUrlScheme:kScheme uiOverrideDelegate:nil stateRestorationIdentifier:@"vcm.connectiq.poc"];
    return YES;
}
@end

@interface SceneDelegate : UIResponder <UIWindowSceneDelegate>
@property(nonatomic, strong) UIWindow *window;
@property(nonatomic, strong) Receiver *receiver;
@end
@implementation SceneDelegate
- (void)scene:(UIScene *)scene willConnectToSession:(UISceneSession *)session options:(UISceneConnectionOptions *)options {
    if (![scene isKindOfClass:UIWindowScene.class]) return;
    self.window = [[UIWindow alloc] initWithWindowScene:(UIWindowScene *)scene];
    self.receiver = [Receiver new];
    self.window.rootViewController = [[UINavigationController alloc] initWithRootViewController:self.receiver];
    [self.window makeKeyAndVisible];
    NSURL *url = options.URLContexts.anyObject.URL;
    if (url) [self.receiver handleURL:url];
}
- (void)scene:(UIScene *)scene openURLContexts:(NSSet<UIOpenURLContext *> *)contexts {
    for (UIOpenURLContext *context in contexts) [self.receiver handleURL:context.URL];
}
@end
int main(int argc, char *argv[]) { @autoreleasepool { return UIApplicationMain(argc, argv, nil, NSStringFromClass(AppDelegate.class)); } }
