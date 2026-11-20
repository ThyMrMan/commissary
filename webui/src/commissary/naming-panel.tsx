import { useEffect, useState } from 'react';

import { apiClient, readJson } from '@/app/api-client';
import {
  useVideoOrganization,
  videoOrganization,
} from '@/features/settings/tabs/video-organization';

import s from './naming-panel.module.css';

/**
 * Commissary: Sonarr/Radarr {Token} naming on the video organization settings
 * (core/commissary/naming_tokens.py). Upstream's React settings page (3.6.7)
 * replaced the vanilla card the fork's commissary-naming-settings.js decorated.
 *
 * Under the movie and episode templates:
 *  - "show all N" lists every {Token} the renderer knows; a chip inserts at the caret
 *  - "use the TRaSH recommended scheme" fills the template with the guide's format
 *  - a template with {Token}s gets the example the SERVER renders (render_path, the
 *    code an import uses): upstream's preview is a JavaScript copy that knows only $tokens
 * And "Rename a title's existing files before importing into it", the fork's own setting.
 *
 * The routes are admin-only: for anyone else this renders nothing.
 */

type Scope = 'movie' | 'episode';

interface Tokens {
  movie: string[];
  episode: string[];
  import_only: string[];
}

const API = 'video/libraries/naming/';
const STYLE: Record<Scope, string> = { movie: 'Radarr', episode: 'Sonarr' };
const FIELD: Record<Scope, string> = { movie: 'movie_template', episode: 'episode_template' };

// trash-guides.info, as the old fork offered them
export const TRASH: Record<Scope, string> = {
  movie:
    '{Movie CleanTitle} {(Release Year)} {edition-{Edition Tags}} {[Custom Formats]}' +
    '{[Quality Full]}{[MediaInfo VideoDynamicRangeType]}' +
    '{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}{[Mediainfo VideoCodec]}' +
    '{-Release Group}',
  episode:
    '{Series CleanTitleWithoutYear} {(Series Year)}/Season {season:00}/' +
    '{Series CleanTitleWithoutYear} {(Series Year)} - S{season:00}E{episode:00} - ' +
    '{Episode CleanTitle:90} {[Custom Formats]}{[Quality Full]}' +
    '{[Mediainfo AudioCodec}{ Mediainfo AudioChannels]}' +
    '{[MediaInfo VideoDynamicRangeType]}{[Mediainfo VideoCodec]}{-Release Group}',
};

const toast = (message: string, type: string) =>
  (window as Window & { showToast?: (m: string, t: string) => void }).showToast?.(message, type);

let tokensAsked: Promise<Tokens | null> | null = null;
/** one request per page load; null when the caller isn't an admin */
function loadTokens(): Promise<Tokens | null> {
  tokensAsked ??= readJson<Tokens>(
    apiClient.get(`${API}tokens`, { searchParams: { t: Date.now() } }),
  ).catch(() => null);
  return tokensAsked;
}

/** for tests: forget the cached token list */
export function resetNamingTokens() {
  tokensAsked = null;
}

function useTokens() {
  const [tokens, setTokens] = useState<Tokens | null>(null);
  useEffect(() => {
    let live = true;
    void loadTokens().then((t) => {
      if (live) setTokens(t);
    });
    return () => {
      live = false;
    };
  }, []);
  return tokens;
}

/** what the server's renderer makes of a {Token} template ('' while unknown) */
function useServerExample(scope: Scope, template: string) {
  const [example, setExample] = useState('');
  useEffect(() => {
    if (!template.includes('{')) {
      setExample('');
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      readJson<Partial<Record<Scope, string>>>(
        apiClient.post(`${API}preview`, { json: { [FIELD[scope]]: template } }),
      )
        .then((d) => {
          if (live) setExample(d[scope] ?? '');
        })
        .catch(() => {
          if (live) setExample('');
        });
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [scope, template]);
  return example;
}

/** a chip's text into the template at the caret, saved */
function insertAtCaret(scope: Scope, current: string, text: string) {
  const input = document.getElementById(`vo-${scope}-template`) as HTMLInputElement | null;
  const start = input?.selectionStart ?? current.length;
  const end = input?.selectionEnd ?? start;
  const next = current.slice(0, start) + text + current.slice(end);
  void videoOrganization.change({ [FIELD[scope]]: next });
  requestAnimationFrame(() => {
    const box = document.getElementById(`vo-${scope}-template`) as HTMLInputElement | null;
    if (!box) return;
    box.focus();
    box.selectionStart = box.selectionEnd = start + text.length;
  });
}

/** the {Token} help under one template */
export function CommissaryTokenHelp({ scope }: { scope: Scope }) {
  const tokens = useTokens();
  const { form } = useVideoOrganization();
  const [open, setOpen] = useState(false);
  const template = String(form[FIELD[scope]] ?? '');
  const example = useServerExample(scope, template);
  if (!tokens) return null;
  const names = tokens[scope] ?? [];
  return (
    <>
      <small className="settings-hint" data-commissary-naming={scope}>
        Or use {STYLE[scope]}-style <code>{'{Token}'}</code> names:{' '}
        <button type="button" className={s.link} onClick={() => setOpen((o) => !o)}>
          {open ? 'hide' : `show all ${names.length}`}
        </button>
        , or{' '}
        <button
          type="button"
          className={s.link}
          onClick={() => {
            void videoOrganization.change({ [FIELD[scope]]: TRASH[scope] });
            toast('TRaSH scheme applied, see the example below', 'success');
          }}
        >
          use the TRaSH recommended scheme
        </button>
        .
      </small>
      {open ? (
        <div className={s.tokens}>
          {names.map((n) => (
            <button
              key={n}
              type="button"
              className={s.token}
              title={
                tokens.import_only.includes(n)
                  ? 'Only an import can fill this one; Rename and Naming Conformance can’t rebuild it for a file already in the library.'
                  : undefined
              }
              onClick={() => insertAtCaret(scope, template, `{${n}}`)}
            >
              {`{${n}}`}
            </button>
          ))}
        </div>
      ) : null}
      {example ? (
        <small className="settings-hint" data-commissary-naming-example={scope}>
          As an import names it: <code>{example}</code>
        </small>
      ) : null}
    </>
  );
}

/** "Rename a title's existing files before importing into it" */
export function CommissaryRenameFirst() {
  const tokens = useTokens(); // admin-only, like the setting
  const [enabled, setEnabled] = useState<boolean | null>(null);
  useEffect(() => {
    if (!tokens) return;
    let live = true;
    readJson<{ enabled: boolean }>(
      apiClient.get(`${API}rename-first`, { searchParams: { t: Date.now() } }),
    )
      .then((d) => {
        if (live) setEnabled(!!d.enabled);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [tokens]);
  if (!tokens) return null;
  const change = (on: boolean) => {
    setEnabled(on);
    readJson(apiClient.post(`${API}rename-first`, { json: { enabled: on } }))
      .then(() => toast('Library organization saved', 'success'))
      .catch(() => {
        setEnabled(!on);
        toast('Could not save library organization', 'error');
      });
  };
  return (
    <div className="form-group">
      <label className="checkbox-label">
        <input
          type="checkbox"
          id="commissary-rename-first"
          checked={!!enabled}
          disabled={enabled === null}
          onChange={(event) => change(event.target.checked)}
        />{' '}
        <span>Rename a title’s existing files before importing into it</span>
      </label>
      <small className="settings-hint">
        Brings a show’s earlier files up to the current template first, so a template change doesn’t
        split it across two folder names. Skipped for a padlocked title; a rename that fails never
        blocks the import.
      </small>
    </div>
  );
}
