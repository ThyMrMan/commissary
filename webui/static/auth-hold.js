/**
 * Hold auth-gated API calls until we know whether we're signed in.
 *
 * At boot, ~15 modules fire a one-shot fetch each (video dashboard, libraries,
 * scan status, issue/watchlist/wishlist counts, YouTube channels, search
 * sources, overlay status, …). None of them can know yet whether a session
 * exists — that answer only arrives when /api/profiles/current returns. With
 * security.require_login on, every one of those is refused, so the login screen
 * opened with a burst of 401s and a console full of red, which is what made a
 * real bug (the hidden Plex PIN panel, 1.7.1) hard to see in the first place.
 *
 * DEFER, never reject. The naive version — fail fast while locked — breaks the
 * ordinary install: for the few hundred ms before /api/profiles/current answers,
 * auth state is unknown there too, so boot fetches would get a synthetic 401,
 * modules would paint their "unavailable" state, and nothing would retry. So a
 * held call waits on a promise instead:
 *   • not locked  → the real request runs, just slightly later. No behaviour
 *                   change at all for an install that never turns login on.
 *   • locked      → resolve to the SAME 401 body the server sends, so callers
 *                   take the exact path they take today, minus the round-trip.
 *
 * Loaded immediately after fetch-dedupe.js so this wrapper is the OUTER one —
 * held requests never reach the dedupe cache, which would otherwise memoise a
 * synthetic 401 for the next 2.5s and starve the real request after unlock.
 *
 * Fail-open by construction: a 10s watchdog releases the hold no matter what,
 * so a JS error or a hung /api/profiles/current can only ever cost a short
 * delay — never a permanently mute app.
 */
(function () {
    'use strict';
    if (typeof window === 'undefined' || !window.fetch || !document.body) return;

    // Mirrors core/security/login_gate.py. These are the only endpoints the
    // login screen itself needs before authentication, so they must never be
    // held — holding /api/profiles/current would deadlock the very request whose
    // answer releases the hold. tests/test_login_boot_hold.py pins this in sync
    // with the server list.
    var ALLOW_GET = ['/api/profiles/current', '/api/setup/status',
                     '/api/auth/recovery-question', '/api/auth/plex/status'];
    var ALLOW_POST = ['/api/auth/login', '/api/auth/logout',
                      '/api/auth/recovery-reset', '/api/auth/plex/start'];

    var raw = window.fetch.bind(window);
    var settled = false;      // fast path once auth is known — no promise hop
    var locked = false;
    var release;
    var known = new Promise(function (r) { release = r; });

    function settle(isLocked) {
        if (settled) return;
        settled = true;
        locked = !!isLocked;
        document.body.classList.remove('auth-pending');
        release(locked);
    }

    // init.js calls this from BOTH branches the moment /api/profiles/current
    // answers: locked=true when it shows a lock screen, false otherwise.
    window.__soulsyncAuthSettled = settle;

    // Watchdog: whatever happens, stop holding. Releasing as "not locked" is the
    // safe direction — worst case a request goes out and the server refuses it,
    // which is exactly today's behaviour.
    setTimeout(function () { settle(false); }, 10000);

    function shouldHold(input, init) {
        if (settled) return false;
        try {
            var url = typeof input === 'string' ? input
                : (input && typeof input === 'object' ? input.url : '') || '';
            if (!url) return false;
            if (url[0] !== '/') {
                var u = new URL(url, window.location.origin);
                if (u.origin !== window.location.origin) return false;
                url = u.pathname + u.search;
            }
            var path = url.split('?')[0];
            if (path.indexOf('/api/') !== 0) return false;
            if (path.indexOf('/socket.io') === 0) return false;
            if (path.indexOf('/stream') !== -1) return false;   // SSE must not be delayed
            // The key-authed public API carries its own credentials and is not
            // session-gated, so it has nothing to wait for.
            if (path.indexOf('/api/v1/') === 0) return false;
            var method = String((init && init.method) ||
                (input && typeof input === 'object' && input.method) || 'GET').toUpperCase();
            var allow = method === 'GET' ? ALLOW_GET : (method === 'POST' ? ALLOW_POST : []);
            return allow.indexOf(path) === -1;
        } catch (e) {
            return false;       // never let this wrapper be the reason a call fails
        }
    }

    window.fetch = function (input, init) {
        if (!shouldHold(input, init)) return raw(input, init);
        return known.then(function (isLocked) {
            if (!isLocked) return raw(input, init);
            return new Response(
                JSON.stringify({ error: 'login_required', login_required: true }),
                { status: 401, headers: { 'Content-Type': 'application/json' } });
        });
    };

    document.body.classList.add('auth-pending');

    // introspection hook for tests/debugging (mirrors _apiGetDedupe)
    window._authHold = {
        shouldHold: shouldHold,
        state: function () { return { settled: settled, locked: locked }; },
    };
})();
