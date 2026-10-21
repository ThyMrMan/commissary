/*
 * Commissary — Sonarr/Radarr {Token} naming on the Library settings card.
 *
 * Under the movie and episode templates:
 *   · "show all N" lists every {Token} the renderer knows; a chip inserts at the caret
 *   · "use the TRaSH recommended scheme" fills the box with the guide's format
 *   · the Example line is rendered by the SERVER (render_path, the code an import
 *     uses), replacing upstream's JavaScript $token-only copy, which shows a
 *     {Token} template as raw text
 *
 * Programmatic writes dispatch 'input' + 'change' so upstream's own handlers
 * preview and save them exactly like typing.
 */
(function () {
    'use strict';
    var API = '/api/video/libraries/naming/';
    var SCOPES = { movie: 'Radarr', episode: 'Sonarr' };
    // trash-guides.info, as the old fork offered them
    var TRASH = {
        movie: '{Movie CleanTitle} {(Release Year)} {edition-{Edition Tags}} {[Custom Formats]}'
             + '{[Quality Full]}{[MediaInfo VideoDynamicRangeType]}'
             + '{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}{[Mediainfo VideoCodec]}'
             + '{-Release Group}',
        episode: '{Series CleanTitleWithoutYear} {(Series Year)}/Season {season:00}/'
               + '{Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} - '
               + '{Episode CleanTitle:90} {[Custom Formats]}{[Quality Full]}'
               + '{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}'
               + '{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}'
    };
    var tokens = null;
    var written = {};      // preview id -> the text this script last wrote
    var timer = null;
    var asked = 0;

    function toast(msg, type) { if (typeof showToast === 'function') showToast(msg, type); }
    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = text;
        return n;
    }
    function box(scope) { return document.getElementById('vo-' + scope + '-template'); }

    function commit(input) {
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
    }

    // ── the Example lines, rendered by the server ────────────────────────────
    function renderPreviews() {
        var body = {};
        ['movie', 'episode', 'youtube'].forEach(function (s) {
            var b = box(s);
            if (b) body[s + '_template'] = b.value || b.placeholder || '';
        });
        var mine = ++asked;
        fetch(API + 'preview', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                 body: JSON.stringify(body) })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d || mine !== asked) return;
                Object.keys(d).forEach(function (s) {
                    var code = document.getElementById('vo-' + s + '-preview');
                    if (!code || !d[s]) return;
                    written[code.id] = d[s];
                    code.textContent = d[s];
                });
            })
            .catch(function () { /* upstream's own preview stays */ });
    }
    function schedule() {
        clearTimeout(timer);
        timer = setTimeout(renderPreviews, 250);
    }

    // ── chips + preset ──────────────────────────────────────────────────────
    function decorate(scope) {
        var input = box(scope);
        if (!input || input.closest('.form-group').querySelector('[data-commissary-naming]')) return;
        var group = input.closest('.form-group');
        var names = (tokens && tokens[scope]) || [];
        var importOnly = (tokens && tokens.import_only) || [];
        var hint = el('small', 'settings-hint');
        hint.setAttribute('data-commissary-naming', scope);
        hint.appendChild(document.createTextNode('Or use ' + SCOPES[scope] + '-style '));
        hint.appendChild(el('code', null, '{Token}'));
        hint.appendChild(document.createTextNode(' names — '));
        var show = el('button', 'commissary-link-btn', 'show all ' + names.length);
        show.type = 'button';
        hint.appendChild(show);
        hint.appendChild(document.createTextNode(', or '));
        var preset = el('button', 'commissary-link-btn', 'use the TRaSH recommended scheme');
        preset.type = 'button';
        hint.appendChild(preset);
        hint.appendChild(document.createTextNode('.'));
        var list = el('div', 'commissary-token-list');
        list.hidden = true;
        names.forEach(function (n) {
            var chip = el('button', 'commissary-token', '{' + n + '}');
            chip.type = 'button';
            if (importOnly.indexOf(n) !== -1) {
                chip.title = 'Only an import can fill this one; Rename and Naming Conformance '
                    + 'can’t rebuild it for a file already in the library.';
            }
            list.appendChild(chip);
        });
        // after the "Variables:" hint, before the Example line
        var first = group.querySelector('.settings-hint');
        var at = first ? first.nextSibling : null;
        group.insertBefore(hint, at);
        group.insertBefore(list, at);

        show.addEventListener('click', function () {
            list.hidden = !list.hidden;
            show.textContent = list.hidden ? 'show all ' + names.length : 'hide';
        });
        list.addEventListener('click', function (e) {
            var chip = e.target.closest('.commissary-token');
            if (!chip) return;
            var text = chip.textContent;
            var s = input.selectionStart == null ? input.value.length : input.selectionStart;
            var t = input.selectionEnd == null ? s : input.selectionEnd;
            input.value = input.value.slice(0, s) + text + input.value.slice(t);
            input.focus();
            input.selectionStart = input.selectionEnd = s + text.length;
            commit(input);
        });
        preset.addEventListener('click', function () {
            input.value = TRASH[scope];
            commit(input);
            toast('TRaSH scheme applied — see the example below', 'success');
        });
    }

    // "Rename existing files before importing" - the fork's own setting (upstream's
    // organization settings drop keys they don't know), after "Replace an existing copy"
    function renameFirst() {
        var replace = document.getElementById('vo-replace');
        if (!replace || document.getElementById('commissary-rename-first')) return;
        var group = el('div', 'form-group');
        var label = el('label', 'checkbox-label');
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.id = 'commissary-rename-first';
        cb.disabled = true;
        label.appendChild(cb);
        label.appendChild(el('span', null, 'Rename a title’s existing files before importing into it'));
        group.appendChild(label);
        group.appendChild(el('small', 'settings-hint', 'Brings a show’s earlier files up to the '
            + 'current template first, so a template change doesn’t split it across two folder '
            + 'names. Skipped for a locked title; a rename that fails never blocks the import.'));
        var after = replace.closest('.form-group');
        after.parentNode.insertBefore(group, after.nextSibling);
        fetch(API + 'rename-first?t=' + Date.now(), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) { if (d) { cb.checked = !!d.enabled; cb.disabled = false; } });
        cb.addEventListener('change', function () {
            fetch(API + 'rename-first', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                         body: JSON.stringify({ enabled: cb.checked }) })
                .then(function (r) { if (!r.ok) throw new Error(); toast('Library organization saved', 'success'); })
                .catch(function () {
                    cb.checked = !cb.checked;
                    toast('Could not save library organization', 'error');
                });
        });
    }

    function attach() {
        if (!box('movie')) return false;
        ['movie', 'episode'].forEach(decorate);
        renameFirst();
        // upstream rewrites the Example lines on every keystroke and on load;
        // follow each of its writes with the server's rendering
        ['movie', 'episode', 'youtube'].forEach(function (s) {
            var code = document.getElementById('vo-' + s + '-preview');
            if (!code || code._commissaryNaming) return;
            code._commissaryNaming = true;
            new MutationObserver(function () {
                if (code.textContent !== written[code.id]) schedule();
            }).observe(code, { childList: true, characterData: true, subtree: true });
        });
        schedule();
        return true;
    }

    function start() {
        fetch(API + 'tokens?t=' + Date.now(), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;          // not an admin: upstream's card as it is
                tokens = d;
                (function wait() { if (!attach()) setTimeout(wait, 1000); })();
            })
            .catch(function () { /* leave the card alone */ });
    }

    window.CommissaryNamingSettings = { TRASH: TRASH };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
    else start();
})();
