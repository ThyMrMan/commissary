/**
 * Commissary: hold boot-time API calls until we know whether we're signed in.
 *
 * At boot ~15 modules fire a one-shot fetch each (dashboard, libraries, scan
 * status, counts, search sources, ...). None can know yet whether a session
 * exists; that answer arrives with /api/profiles/current. With login (or the
 * launch PIN) on, every one of them is refused, so the sign-in screen opens
 * with a burst of 401s.
 *
 * DEFER, never reject. Failing fast while auth is unknown would break ordinary
 * installs too (boot fetches would get a synthetic 401 and paint "unavailable"
 * with nothing retrying). A held call waits on a promise:
 *   - not locked -> the real request runs, a moment later
 *   - locked     -> resolves to the same 401 body the server sends, so callers
 *                   take the exact path they take today, minus the round-trip
 *
 * No hook in upstream code: this reads /api/profiles/current answers itself
 * (login_required / launch_pin_required = locked).
 *
 * While locked, later calls are answered locally too: a few modules fire
 * after the verdict, and the server would only refuse them. That lasts until
 * the page unlocks. Password and Plex sign-in reload the page; the launch PIN
 * (verify-launch-pin, reset-pin-via-credential) unlocks in place, so a
 * successful answer from either, or a later /current that isn't locked,
 * releases it. The endpoints the
 * sign-in screens need come from the server's own gate lists via
 * window.COMMISSARY.authOpen, so they cannot drift from core/security/*.
 *
 * Loaded right after fetch-dedupe.js so this wrapper is the OUTER one: a held
 * request must never reach the dedupe cache, which would memoise the synthetic
 * 401 and starve the real request after unlock. Fail-open: a 10s watchdog
 * releases the hold whatever happens.
 */
(function () {
    'use strict';
    if (typeof window === 'undefined' || !window.fetch) return;

    var open = (window.COMMISSARY && window.COMMISSARY.authOpen) || null;
    if (!open) return;   // no server list: hold nothing rather than guess
    var ALLOW_GET = open.get || [];
    var ALLOW_POST = open.post || [];
    var CURRENT = '/api/profiles/current';
    var UNLOCKS = ['/api/profiles/verify-launch-pin', '/api/profiles/reset-pin-via-credential'];

    var raw = window.fetch.bind(window);
    var settled = false;
    var locked = false;
    var release;
    var known = new Promise(function (r) { release = r; });

    function settle(isLocked) {
        if (settled) return;
        settled = true;
        locked = !!isLocked;
        release(locked);
    }
    setTimeout(function () { settle(false); }, 10000);

    function pathOf(input) {
        var url = typeof input === 'string' ? input
            : (input && typeof input === 'object' ? (input.url || input.href) : '') || '';
        if (!url) return '';
        if (url[0] !== '/' || url[1] === '/') {
            var u = new URL(url, window.location.origin);
            if (u.origin !== window.location.origin) return '';
            url = u.pathname;
        }
        url = url.split('?')[0].split('#')[0];
        var strip = window.SoulSyncURL && window.SoulSyncURL.strip;
        return strip ? strip(url) : url;
    }

    function isOpen(path, method) {
        if (method === 'GET' && ALLOW_GET.indexOf(path) !== -1) return true;
        if (method === 'POST' && ALLOW_POST.indexOf(path) !== -1) return true;
        if (path.indexOf('/api/invite/') === 0 && (method === 'GET' || method === 'POST')) return true;
        return method === 'GET' && /^\/api\/profiles\/\d+\/avatar$/.test(path);
    }

    function shouldHold(input, init) {
        if (settled && !locked) return false;
        try {
            var path = pathOf(input);
            if (path.indexOf('/api/') !== 0) return false;
            if (path.indexOf('/api/v1/') === 0 && path.indexOf('/api/v1/api-keys-internal') !== 0) return false;
            if (path.indexOf('/stream') !== -1) return false;     // SSE must not be delayed
            var method = String((init && init.method) ||
                (input && typeof input === 'object' && input.method) || 'GET').toUpperCase();
            return !isOpen(path, method);
        } catch (e) {
            return false;       // never let this wrapper be the reason a call fails
        }
    }

    // reads a copy of the answer; the caller still gets the untouched response
    function watch(promise, onData) {
        promise.then(function (resp) {
            if (!resp || !resp.clone) return onData(null);
            resp.clone().json().then(onData, function () { onData(null); });
        }, function () { onData(null); });
        return promise;
    }

    function onCurrent(data) {
        var isLocked = !!(data && (data.login_required || data.launch_pin_required));
        if (!settled) settle(isLocked);
        else if (locked && data && !isLocked) locked = false;
    }

    function onUnlock(data) {
        if (data && data.success) locked = false;
    }

    function unauthorized() {
        return new Response(
            JSON.stringify({ error: 'login_required', login_required: true }),
            { status: 401, headers: { 'Content-Type': 'application/json' } });
    }

    window.fetch = function (input, init) {
        if (!settled || locked) {
            try {
                var path = pathOf(input);
                if (path === CURRENT) return watch(raw(input, init), onCurrent);
                if (UNLOCKS.indexOf(path) !== -1) return watch(raw(input, init), onUnlock);
            } catch (e) { /* fall through to the plain call */ }
        }
        if (!shouldHold(input, init)) return raw(input, init);
        if (settled) return Promise.resolve(unauthorized());    // still locked
        return known.then(function () {
            return locked ? unauthorized() : raw(input, init);
        });
    };

    window._commissaryAuthHold = {
        shouldHold: shouldHold,
        settle: settle,
        state: function () { return { settled: settled, locked: locked }; },
    };
})();
