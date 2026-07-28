/*
 * SoulSync — clear button for the library-style search fields.
 *
 * One implementation for every `.library-search-container`: the video Library,
 * the music Library and Purchased all use the same markup, and three copies of
 * a five-line behaviour is three places to drift.
 *
 * Fully delegated — the containers exist in the page from the start, but the
 * pages re-render around them, and a listener bound to the element itself would
 * be thrown away. Nothing here reaches into a page's own search handling: the
 * button clears the input and fires a normal `input` event, so whatever debounce
 * / reload each page already has runs exactly as if the user had deleted the
 * text by hand. That is the whole point — no page-specific wiring to keep in
 * step, and no second code path that can filter differently from typing.
 */
(function () {
    'use strict';

    var INPUT = '.library-search-input';
    var CLEAR = '[data-search-clear]';

    function sync(input) {
        var box = input && input.closest('.library-search-container');
        var btn = box && box.querySelector(CLEAR);
        if (btn) btn.hidden = !String(input.value || '').length;
    }

    // Paint on load AND whenever a page is shown: a field can already hold text
    // (restored state, or a page revisited) and the button must match it.
    function syncAll() {
        var inputs = document.querySelectorAll(INPUT);
        for (var i = 0; i < inputs.length; i++) sync(inputs[i]);
    }

    function clear(btn) {
        var box = btn.closest('.library-search-container');
        var input = box && box.querySelector(INPUT);
        if (!input) return;
        if (!input.value) { input.focus(); return; }
        input.value = '';
        // The page's own handler does the filtering. Dispatching the same event
        // typing produces keeps this from becoming a second, divergent path.
        input.dispatchEvent(new Event('input', { bubbles: true }));
        sync(input);
        input.focus();
    }

    document.addEventListener('input', function (e) {
        if (e.target && e.target.matches && e.target.matches(INPUT)) sync(e.target);
    });

    document.addEventListener('click', function (e) {
        var btn = e.target.closest && e.target.closest(CLEAR);
        if (btn) { e.preventDefault(); clear(btn); }
    });

    // Escape clears too — the conventional shortcut, and it costs nothing.
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || !e.target || !e.target.matches || !e.target.matches(INPUT)) return;
        if (!e.target.value) return;            // let Escape bubble (close a modal etc.)
        e.stopPropagation();
        e.target.value = '';
        e.target.dispatchEvent(new Event('input', { bubbles: true }));
        sync(e.target);
    });

    document.addEventListener('soulsync:video-page-shown', syncAll);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', syncAll);
    else syncAll();
})();
