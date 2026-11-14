#import <UIKit/UIKit.h>
#import <ConnectIQ/ConnectIQ.h>

static NSString *const kScheme = @"vcm-iq-poc-7ac29246";
static NSString *const kAppUUID = @"7ac29246-e724-4d45-a145-41d19c543680";

@interface Receiver : UIViewController <IQDeviceEventDelegate, IQAppMessageDelegate>
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
    UIStackView *stack = [[UIStackView alloc] initWithArrangedSubviews:@[choose, self.status, self.latest, share]];
    stack.axis = UILayoutConstraintAxisVertical; stack.spacing = 20; stack.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:stack];
    [NSLayoutConstraint activateConstraints:@[[stack.leadingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.leadingAnchor constant:20], [stack.trailingAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.trailingAnchor constant:-20], [stack.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor constant:24]]];
    NSString *name = [NSString stringWithFormat:@"vcm-%@.jsonl", NSUUID.UUID.UUIDString];
    self.logURL = [[NSFileManager.defaultManager URLsForDirectory:NSDocumentDirectory inDomains:NSUserDomainMask].firstObject URLByAppendingPathComponent:name];
    IQDevice *saved = [self restoreDevice];
    if (saved) [self useDevice:saved];
}
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
