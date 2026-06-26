/**
 * extension/prefetch.js
 *
 * Runs at document_start — the earliest possible moment, while the page HTML
 * is still being parsed. Fires /session/init immediately so the response is
 * ready (or nearly ready) by the time widget.js mounts and the user opens
 * the chat panel.
 *
 * Stores the result on window.__dkSessionPromise (content-script isolated
 * world — shared with widget.js because both scripts are from the same
 * extension and run in the same isolated world).
 */
(function () {
    "use strict";

    var PROFILE_KEY = "dk_advisor_id";
    var API_BASE = "http://localhost:8000";

    var profileId;
    try {
        profileId = localStorage.getItem(PROFILE_KEY);
        if (!profileId) {
            profileId = (typeof crypto !== "undefined" && crypto.randomUUID && crypto.randomUUID()) ||
                "dk-" + Date.now() + "-" + Math.random().toString(36).slice(2);
            localStorage.setItem(PROFILE_KEY, profileId);
        }
    } catch (e) {
        profileId = "dk-" + Date.now() + "-" + Math.random().toString(36).slice(2);
    }

    window.__dkSessionPromise = fetch(API_BASE + "/session/init", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-Profile-Id": profileId,
        },
        body: JSON.stringify({}),
    })
    .then(function (r) { return r.ok ? r.json() : null; })
    .catch(function () { return null; });
})();
