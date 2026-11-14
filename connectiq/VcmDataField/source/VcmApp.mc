using Toybox.Application;
using Toybox.WatchUi;
class VcmApp extends Application.AppBase {
    function initialize() { AppBase.initialize(); }
    function getInitialView() { return [new VcmField()]; }
}
