/**
 * extension/test_inject.js
 *
 * Tests inject.js's DOM logic using jsdom + a mocked `chrome` global
 * (the real chrome.* APIs only exist inside an actual extension
 * context, so this mocks just enough of chrome.runtime.getURL to
 * verify inject.js builds and inserts the script tag correctly).
 *
 * Usage: node extension/test_inject.js
 */

const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

let passed = 0;
let failed = 0;

function check(label, condition) {
    if (condition) {
        passed++;
        console.log("  \u2713 " + label);
    } else {
        failed++;
        console.log("  \u2717 " + label);
    }
}

function freshDom() {
    const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
        url: "https://www.dotandkey.com/",
        runScripts: "outside-only",
    });
    dom.window.chrome = {
        runtime: {
            getURL: function (file) {
                return "chrome-extension://fake-extension-id/" + file;
            },
        },
    };
    return dom;
}

function run() {
    console.log("\ninject.js tests");

    const injectSrc = fs.readFileSync(path.join(__dirname, "inject.js"), "utf8");

    // ---- basic injection ----
    const dom1 = freshDom();
    dom1.window.eval(injectSrc);

    const script = dom1.window.document.getElementById("dk-advisor-injected-script");
    check("script tag injected", !!script);
    check("script tag is an actual <script> element", script && script.tagName === "SCRIPT");
    check("src points at widget.js via chrome.runtime.getURL",
        script && script.src === "chrome-extension://fake-extension-id/widget.js");
    check("data-api-base attribute set for local dev backend",
        script && script.getAttribute("data-api-base") === "http://localhost:8000");
    check("script appended to document.body",
        dom1.window.document.body.contains(script));

    // ---- idempotency: running twice must not double-inject ----
    const dom2 = freshDom();
    dom2.window.eval(injectSrc);
    dom2.window.eval(injectSrc); // run again, simulating a re-injection edge case

    const allScripts = dom2.window.document.querySelectorAll("#dk-advisor-injected-script");
    check("running inject.js twice does not create duplicate script tags",
        allScripts.length === 1);

    console.log("\n" + "=".repeat(50));
    console.log(passed + " passed, " + failed + " failed");
    process.exit(failed > 0 ? 1 : 0);
}

run();