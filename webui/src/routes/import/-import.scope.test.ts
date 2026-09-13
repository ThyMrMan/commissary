import { describe, expect, it } from 'vitest';

import type { ImportAlbumMatch, ImportStagingFile } from './-import.types';

import { getDisplayedMatchFile, getUnmatchedStagingFiles } from './-import.helpers';

/**
 * The manual-assign pool on the album match screen.
 *
 * Reported: matching an album "often seems to get confused, auto matching
 * things that are clearly outside of that album's folder in the downloads
 * folder". The pool offered every file in the scanned folder, and it compared
 * files by FILENAME -- a download folder holds many "01 - Intro.flac"s, so a
 * same-named file from another album was hidden or treated as this one.
 */

function file(fullPath: string): ImportStagingFile {
  const filename = fullPath.split('/').pop() ?? fullPath;
  return { filename, full_path: fullPath } as ImportStagingFile;
}

function match(stagingFile: ImportStagingFile | null): ImportAlbumMatch {
  return {
    track: { name: 'Intro', track_number: 1 },
    staging_file: stagingFile,
    confidence: 0.9,
  } as ImportAlbumMatch;
}

describe('getUnmatchedStagingFiles', () => {
  it('offers only the files the match drew from', () => {
    const files = [
      file('/dl/Album/01 Intro.flac'),
      file('/dl/Album/02 Song.flac'),
      file('/dl/Other/01 Song.flac'),
    ];
    const pool = getUnmatchedStagingFiles([], files, {}, [
      '/dl/Album/01 Intro.flac',
      '/dl/Album/02 Song.flac',
    ]);

    expect(pool.map((entry) => entry.file.full_path)).toEqual([
      '/dl/Album/01 Intro.flac',
      '/dl/Album/02 Song.flac',
    ]);
  });

  it('keeps indices pointing into the full file list', () => {
    // Overrides store positions in the page's whole file list, so filtering
    // must not renumber what it keeps.
    const files = [file('/dl/Other/x.flac'), file('/dl/Album/01 Intro.flac')];
    const pool = getUnmatchedStagingFiles([], files, {}, ['/dl/Album/01 Intro.flac']);

    expect(pool).toEqual([{ file: files[1], index: 1 }]);
  });

  it('still offers every file when the match was not scoped', () => {
    const files = [file('/dl/A/a.flac'), file('/dl/B/b.flac')];

    expect(getUnmatchedStagingFiles([], files, {}, null)).toHaveLength(2);
    expect(getUnmatchedStagingFiles([], files, {})).toHaveLength(2);
  });

  it('does not hide a same-named file from a different folder', () => {
    const used = file('/dl/Album/01 Intro.flac');
    const other = file('/dl/Other/01 Intro.flac');
    const pool = getUnmatchedStagingFiles([match(used)], [used, other], {});

    expect(pool.map((entry) => entry.file.full_path)).toEqual(['/dl/Other/01 Intro.flac']);
  });
});

describe('getDisplayedMatchFile across folders', () => {
  it('does not treat a same-named file elsewhere as a reassignment', () => {
    const own = file('/dl/Album/01 Intro.flac');
    const other = file('/dl/Other/01 Intro.flac');
    // Track 1 was handed the OTHER folder's Intro; track 0 keeps its own.
    const result = getDisplayedMatchFile(match(own), 0, [own, other], { 1: 1 });

    expect(result.file?.full_path).toBe('/dl/Album/01 Intro.flac');
  });
});
