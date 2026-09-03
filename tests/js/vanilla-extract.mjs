// Lift real functions out of a vanilla page so they can be TESTED, rather than
// pinned by asserting that some substring appears in the file.
//
// Ported from upstream SoulSync 3.3.1 (webui/src/test/vanilla-extract.ts), where
// it exists to compare a React port against the code it replaces. This fork has
// no React Discover, so the same tool is used for the other half of its value:
// webui/static/discover.js is 12k lines and its only coverage is source-text
// pins, which cannot see a return value or whether a branch is even reachable.
// Two such pins in this repo passed with the feature disabled.
//
// Kept as .mjs under tests/js/ rather than TypeScript under vitest because that
// is where this fork's vanilla-page harnesses already live and what its pytest
// run executes. Upstream's TS version is the one to take if Discover is ever
// migrated and differential tests are wanted.
//
// The brace matcher is upstream's, comments included: each one records a real
// extraction that failed before the case was handled, which is exactly the sort
// of knowledge that should not be re-learned.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));

/** The live page. There is no frozen fixture here — this fork still ships it. */
export const VANILLA_DISCOVER = readFileSync(
    join(HERE, '..', '..', 'webui', 'static', 'discover.js'), 'utf8');

/**
 * Lift one function out by brace-matching, string- and regex-literal aware.
 *
 * A regex cannot do this — the bodies contain nested braces, template literals
 * and regex literals with braces in them.
 *
 * The body's opening brace is found by first walking the PARAMETER LIST to its
 * closing paren. Jumping to the first `{` after the declaration looks
 * equivalent and is not: a default-valued object parameter (`opts = {}`) puts a
 * brace inside the parameter list, and matching from there returns a truncated
 * function that fails to parse.
 */
export function extractFunction(name, source = VANILLA_DISCOVER) {
    const decl = new RegExp(`^(?:async )?function ${name}\\s*\\(`, 'm');
    const m = decl.exec(source);
    if (!m) throw new Error(`vanilla function ${name} not found`);

    // Walk the parameter list to its matching ')', then take the next '{'.
    let p = source.indexOf('(', m.index);
    let parens = 0;
    for (; p < source.length; p++) {
        if (source[p] === '(') parens++;
        else if (source[p] === ')') {
            parens--;
            if (parens === 0) break;
        }
    }
    let i = source.indexOf('{', p);
    let depth = 0;
    // 'code' | 'tmpl' — plus a stack recording the brace depth each `${` opened
    // at, so a nested template inside a substitution returns to the right one.
    let mode = 'code';
    const tmplStack = [];
    // The last significant character, which is how a `/` is classified: after a
    // value it is division, after an operator or opener it starts a regex.
    let prev = '';

    const REGEX_PRECEDERS = '(,=:[!&|?{};+-*%~^<>\n';

    for (; i < source.length; i++) {
        const c = source[i];

        if (mode === 'tmpl') {
            // Inside a template literal. A backtick closes it; `${` re-enters code.
            if (c === '\\') { i++; continue; }
            if (c === '`') { mode = 'code'; prev = '`'; continue; }
            if (c === '$' && source[i + 1] === '{') {
                tmplStack.push(depth);
                depth++;
                mode = 'code';
                i++;
                prev = '{';
                continue;
            }
            continue;
        }

        // Comments. An apostrophe in prose would otherwise open a phantom
        // string that swallows the rest of the function.
        if (c === '/' && source[i + 1] === '/') {
            while (i < source.length && source[i] !== '\n') i++;
            continue;
        }
        if (c === '/' && source[i + 1] === '*') {
            i += 2;
            while (i < source.length && !(source[i] === '*' && source[i + 1] === '/')) i++;
            i++;
            continue;
        }

        // A regex literal. `/"/g` inside `.replace(/"/g, '&quot;')` would
        // otherwise read as a division followed by an unterminated string, and
        // everything after it desyncs. Character classes are skipped wholesale
        // so a `/` inside `[^/]` cannot end it early.
        if (c === '/' && REGEX_PRECEDERS.includes(prev)) {
            i++;
            let inClass = false;
            for (; i < source.length; i++) {
                const r = source[i];
                if (r === '\\') { i++; continue; }
                if (r === '[') inClass = true;
                else if (r === ']') inClass = false;
                else if (r === '/' && !inClass) break;
            }
            prev = '/';
            continue;
        }

        if (c === '"' || c === "'") {
            const quote = c;
            i++;
            for (; i < source.length; i++) {
                if (source[i] === '\\') { i++; continue; }
                if (source[i] === quote) break;
            }
            prev = quote;
            continue;
        }

        // A template literal. NESTING is the point: a template whose
        // substitution contains another template closes the outer one on the
        // inner's opener if every backtick is treated as a plain toggle.
        if (c === '`') { mode = 'tmpl'; continue; }

        if (c === '{') { depth++; prev = '{'; continue; }
        if (c === '}') {
            depth--;
            // Closing a `${…}` returns to the template that opened it.
            if (tmplStack.length && depth === tmplStack[tmplStack.length - 1]) {
                tmplStack.pop();
                mode = 'tmpl';
                continue;
            }
            if (depth === 0) return source.slice(m.index, i + 1);
            prev = '}';
            continue;
        }

        if (!/\s/.test(c) || c === '\n') prev = c;
    }
    throw new Error(`unbalanced braces extracting ${name}`);
}

/**
 * Evaluate the named vanilla functions in a scratch scope.
 *
 * `extraPreamble` supplies whatever module state or collaborators the extracted
 * bodies close over; `extraExports` returns those bindings so a test can see
 * what the vanilla wrote to them.
 *
 * Unlike upstream this does NOT neutralise `escapeHtml`: there is no React port
 * here to compare against, so escaping is the vanilla's own job and worth
 * testing rather than stubbing out. A caller that wants it neutral can say so
 * in its own preamble.
 */
export function loadVanilla(names, extraPreamble = '', extraExports = []) {
    const body = names.map((n) => extractFunction(n)).join('\n');
    const exports = [...names, ...extraExports].join(', ');
    // eslint-disable-next-line no-new-func
    return new Function(`${extraPreamble}\n${body}\nreturn { ${exports} };`)();
}
