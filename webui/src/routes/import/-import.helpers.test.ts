import { describe, expect, it } from 'vitest';

import type { ImportAlbumMatch, ImportStagingFile } from './-import.types';

import { getDisplayedMatchFile } from './-import.helpers';

/**
 * A file the user assigned by hand carries a hardcoded confidence of 1, so the
 * row rendered "100%" — which reads as "the matcher was certain", exactly
 * backwards when you are looking at that row BECAUSE the matcher failed to
 * find the file. `isOverride` is what lets the UI say "manual" instead, so
 * that flag is the contract worth pinning.
 */

function file(filename: string): ImportStagingFile {
  return { filename, full_path: `/stage/${filename}` } as ImportStagingFile;
}

function match(overrides: Partial<ImportAlbumMatch> = {}): ImportAlbumMatch {
  return {
    track: { name: 'Blue Blood', track_number: 2 },
    staging_file: null,
    confidence: 0,
    ...overrides,
  } as ImportAlbumMatch;
}

describe('getDisplayedMatchFile', () => {
  it('flags a hand-assigned file as an override', () => {
    const files = [file('01 Blue Blood.flac')];
    const result = getDisplayedMatchFile(match(), 0, files, { 0: 0 });

    expect(result.isOverride).toBe(true);
    expect(result.file?.filename).toBe('01 Blue Blood.flac');
    // The 1 is a placeholder for "the user chose it", NOT a measurement —
    // which is why the row must not render it as a percentage.
    expect(result.confidence).toBe(1);
  });

  it('does not flag a file the matcher found itself', () => {
    const found = file('02 Week End.flac');
    const result = getDisplayedMatchFile(
      match({ staging_file: found, confidence: 0.525 }),
      0,
      [found],
      {},
    );

    expect(result.isOverride).toBe(false);
    expect(result.confidence).toBeCloseTo(0.525);
  });

  it('reports no override when a track was explicitly unmatched', () => {
    const result = getDisplayedMatchFile(
      match({ staging_file: file('x.flac'), confidence: 0.9 }),
      0,
      [file('x.flac')],
      { 0: -1 },
    );

    expect(result.file).toBeNull();
    expect(result.isOverride).toBe(false);
  });

  it('drops an auto match whose file was reassigned to another track', () => {
    const shared = file('01 Blue Blood.flac');
    const result = getDisplayedMatchFile(
      match({ staging_file: shared, confidence: 0.6 }),
      0,
      [shared],
      { 1: 0 },
    );

    expect(result.file).toBeNull();
    expect(result.isOverride).toBe(false);
  });
});
