// Behavioral harness for webui/static/commissary-auth-hold.js, run under node
// 18+ (real Response objects). Exits non-zero with a message on any failure.
// Each scenario evals a fresh copy of the script against a fake fetch.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', '..', 'webui', 'static', 'commissary-auth-hold.js'), 'utf8');
const OPEN = JSON.parse(process.argv[2] || 'null');

function assert(cond, msg) {
    if (!cond) { console.error('FAIL: ' + msg); process.exit(1); }
}

const tick = () => new Promise(r => setTimeout(r, 5));

function load(currentBody, { base = '' } = {}) {
    const calls = [];
    const fake = (input, init) => {
        const url = typeof input === 'string' ? input : input.url;
        calls.push({ url, method: (init && init.method) || 'GET' });
        const body = url.endsWith('/api/profiles/current') ? currentBody : { ok: true, url };
        return Promise.resolve(new Response(JSON.stringify(body), {
            status: 200, headers: { 'Content-Type': 'application/json' } }));
    };
    globalThis.window = {
        fetch: fake,
        location: { origin: 'http://localhost:8008' },
        COMMISSARY: { authOpen: OPEN },
        SoulSyncURL: base ? { strip: p => (p === base || p.startsWith(base + '/')) ? p.slice(base.length) || '/' : p } : undefined,
    };
    (0, eval)(src);
    return { fetch: globalThis.window.fetch, calls, hook: globalThis.window._commissaryAuthHold };
}

const run = async () => {
    // 1) login on: boot calls are parked, then answered locally with 401 —
    //    they never reach the server.
    {
        const { fetch, calls, hook } = load({ success: false, login_required: true });
        const parked = fetch('/api/video/dashboard');
        await tick();
        assert(calls.length === 0, 'a boot call must be held until auth is known');
        const cur = await fetch('/api/profiles/current');
        assert((await cur.json()).login_required === true, 'caller still reads /current normally');
        const r = await parked;
        assert(r.status === 401, `locked: held call resolves to 401, got ${r.status}`);
        assert((await r.json()).login_required === true, 'same body the server sends');
        assert(calls.length === 1, `locked: only /current went out, saw ${calls.map(c => c.url)}`);
        assert(hook.state().locked === true, 'state is locked');
    }

    // 2) login off: DEFER, not reject — the held call goes out for real.
    {
        const { fetch, calls } = load({ success: true, profile: { id: 1 } });
        const parked = fetch('/api/video/dashboard');
        await fetch('/api/profiles/current');
        const r = await parked;
        assert(r.status === 200, `unlocked: real response, got ${r.status}`);
        assert(calls.some(c => c.url === '/api/video/dashboard'), 'the real request ran');
    }

    // 3) launch PIN counts as locked
    {
        const { fetch } = load({ success: true, profile: { id: 1 }, launch_pin_required: true });
        const parked = fetch('/api/issues/counts');
        await fetch('/api/profiles/current');
        assert((await parked).status === 401, 'launch PIN lock answers held calls locally');
    }

    // 3b) calls fired AFTER a locked verdict are answered locally as well
    {
        const { fetch, calls } = load({ success: false, login_required: true });
        await fetch('/api/profiles/current');
        await tick();
        const late = await fetch('/api/repair/status');
        assert(late.status === 401, 'a straggler after the lock gets the local 401');
        assert(!calls.some(c => c.url === '/api/repair/status'), 'and never reaches the server');
        const open = await fetch('/api/auth/plex/available');
        assert(open.status === 200, 'sign-in endpoints still go out while locked');
    }

    // 3c) the launch PIN unlocks in place (no reload): a successful verify
    //     releases the lock, a wrong PIN keeps it
    {
        const calls = [];
        let verifyOk = false;
        globalThis.window = {
            fetch: (input, init) => {
                const url = typeof input === 'string' ? input : input.url;
                calls.push(url);
                let body = { ok: true };
                if (url === '/api/profiles/current') body = { success: true, profile: { id: 1 }, launch_pin_required: true };
                if (url === '/api/profiles/verify-launch-pin') body = { success: verifyOk };
                return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
            },
            location: { origin: 'http://localhost:8008' },
            COMMISSARY: { authOpen: OPEN },
        };
        (0, eval)(src);
        const f = globalThis.window.fetch;
        const hook = globalThis.window._commissaryAuthHold;
        await f('/api/profiles/current');
        await tick();
        assert(hook.state().locked, 'launch PIN locks');
        await f('/api/profiles/verify-launch-pin', { method: 'POST' });
        await tick();
        assert(hook.state().locked, 'a wrong PIN keeps the lock');
        verifyOk = true;
        await f('/api/profiles/verify-launch-pin', { method: 'POST' });
        await tick();
        assert(!hook.state().locked, 'a correct PIN releases it');
        const r = await f('/api/video/dashboard');
        assert(r.status === 200 && calls.includes('/api/video/dashboard'), 'the app loads for real after unlock');
    }

    // 3d) a later /current that is no longer locked releases it too
    {
        let lockedNow = true;
        globalThis.window = {
            fetch: (input) => Promise.resolve(new Response(JSON.stringify(
                input === '/api/profiles/current' ? { success: true, launch_pin_required: lockedNow } : { ok: 1 }), { status: 200 })),
            location: { origin: 'http://localhost:8008' },
            COMMISSARY: { authOpen: OPEN },
        };
        (0, eval)(src);
        const f = globalThis.window.fetch;
        await f('/api/profiles/current');
        await tick();
        lockedNow = false;
        await f('/api/profiles/current');
        await tick();
        assert(!globalThis.window._commissaryAuthHold.state().locked, 'an unlocked /current releases the hold');
    }

    // 4) every endpoint a lock screen needs is never held (from the server list)
    {
        const { hook } = load({});
        for (const p of OPEN.get) assert(!hook.shouldHold(p, { method: 'GET' }), `GET ${p} must not be held`);
        for (const p of OPEN.post) assert(!hook.shouldHold(p, { method: 'POST' }), `POST ${p} must not be held`);
        assert(!hook.shouldHold('/api/invite/abc', { method: 'POST' }), 'invite links stay open');
        assert(!hook.shouldHold('/api/profiles/3/avatar'), 'picker avatars stay open');
        assert(hook.shouldHold('/api/profiles/3/avatar', { method: 'DELETE' }), 'only GET avatars');
        assert(hook.shouldHold('/api/settings', { method: 'POST' }), 'a normal call is held');
    }

    // 5) never delayed: key-authed v1, streams, non-API, cross-origin
    {
        const { hook } = load({});
        assert(!hook.shouldHold('/api/v1/library/artists'), 'key-authed v1 is not session-gated');
        assert(hook.shouldHold('/api/v1/api-keys-internal'), '...except its session-authed part');
        assert(!hook.shouldHold('/api/downloads/stream'), 'SSE is never delayed');
        assert(!hook.shouldHold('/static/app.css'), 'non-API passes');
        assert(!hook.shouldHold('https://example.com/api/x'), 'cross-origin passes');
        assert(hook.shouldHold('http://localhost:8008/api/x?y=1'), 'absolute same-origin URL is held');
        assert(hook.shouldHold(new Request('http://localhost:8008/api/x')), 'Request objects are held');
    }

    // 6) behind a URL base path the same rules apply to the stripped path
    {
        const { hook } = load({}, { base: '/soulsync' });
        assert(!hook.shouldHold('/soulsync/api/profiles/current'), '/current under a base path is open');
        assert(hook.shouldHold('/soulsync/api/settings', { method: 'POST' }), 'prefixed call is held');
    }

    // 7) first verdict wins; a broken /current answer fails open
    {
        const { hook } = load({});
        hook.settle(true);
        hook.settle(false);
        assert(hook.state().locked === true, 'settle is first-call-wins');
    }
    {
        const calls = [];
        globalThis.window = {
            fetch: (input) => { calls.push(input); return Promise.resolve(new Response('not json', { status: 500 })); },
            location: { origin: 'http://localhost:8008' },
            COMMISSARY: { authOpen: OPEN },
        };
        (0, eval)(src);
        const f = globalThis.window.fetch;
        const parked = f('/api/x');
        await f('/api/profiles/current');
        await parked;
        assert(calls.includes('/api/x'), 'unparseable /current releases as unlocked (fail open)');
    }

    // 8) no server list -> the wrapper does not install at all
    {
        const fake = () => Promise.resolve(new Response('{}'));
        globalThis.window = { fetch: fake, location: { origin: 'http://localhost:8008' } };
        (0, eval)(src);
        assert(globalThis.window.fetch === fake, 'without authOpen nothing is wrapped');
    }

    // 9) the watchdog is there (10s), so a hang can only cost a delay
    assert(/setTimeout\(function \(\) \{ settle\(false\); \}, 10000\)/.test(src), 'watchdog releases as unlocked');

    console.log('all assertions passed');
    process.exit(0);
};

run().catch(e => { console.error('FAIL: ' + (e && e.stack || e)); process.exit(1); });
