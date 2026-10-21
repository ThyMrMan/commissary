import { cleanup, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { BasicSearchBar } from '@/routes/search/-ui/basic-search-bar';

import { CommissarySourceOptions } from './source-options';

afterEach(cleanup);

function options(sources: Parameters<typeof CommissarySourceOptions>[0]['sources']) {
  const { container } = render(
    <select>
      <CommissarySourceOptions sources={sources} />
    </select>,
  );
  return container.querySelector('select')!;
}

describe('CommissarySourceOptions', () => {
  it('groups the sources outside the download order below it', () => {
    const select = options([
      { name: 'soulseek', display_name: 'Soulseek', in_chain: true },
      { name: 'qobuz', display_name: 'Qobuz', in_chain: false },
    ]);
    const groups = [...select.querySelectorAll('optgroup')].map((g) => [
      g.label,
      [...g.querySelectorAll('option')].map((o) => o.value),
    ]);
    expect(groups).toEqual([
      ['Your download order', ['soulseek']],
      ['Other sources', ['qobuz']],
    ]);
  });

  it('stays a flat list when there is nothing to tell apart', () => {
    const select = options([
      { name: 'soulseek', display_name: 'Soulseek' },
      { name: 'tidal', display_name: 'Tidal' },
    ]);
    expect(select.querySelector('optgroup')).toBeNull();
    expect([...select.options].map((o) => o.value)).toEqual(['soulseek', 'tidal']);
  });
});

describe('the Search page bar', () => {
  it('uses the grouped options', () => {
    const { container } = render(
      <BasicSearchBar
        query=""
        searching={false}
        sources={[
          { name: 'soulseek', display_name: 'Soulseek', in_chain: true },
          { name: 'hifi', display_name: 'HiFi', in_chain: false },
        ]}
        activeSource="soulseek"
        singleSource={false}
        onQueryChange={() => {}}
        onSubmit={() => {}}
        onCancel={() => {}}
        onSelectSource={() => {}}
      />,
    );
    expect(
      [...container.querySelectorAll('#bs-source-row optgroup')].map((g) =>
        g.getAttribute('label'),
      ),
    ).toEqual(['Your download order', 'Other sources']);
  });
});
