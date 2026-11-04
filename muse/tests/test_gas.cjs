const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
let age = 60;
const context = {
  PropertiesService: { getScriptProperties: () => ({ getProperty: key => ({LAST_MAIL_ID:"mail",LAST_SESSION_ID:"session"})[key] }) },
  GmailApp: { search: () => [{ getMessages: () => [{
    getId: () => "mail", getDate: () => new Date(Date.now() - age * 60000),
    getBody: () => "https://livetrack.garmin.com/session/aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa/token/abcdef"
  }] }] }
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(require("node:path").join(__dirname, "../gas/livetrack_detector.gs"), "utf8"), context);
assert.equal(context.findAndPost_().found, false);
age = 5;
assert.equal(context.findAndPost_().found, true);
console.log("PASS GAS freshness precedes duplicate detection");
