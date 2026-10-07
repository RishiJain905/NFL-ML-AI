import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { resetRefreshes } from '../../api/client';
import { mockApi, renderApp } from '../../test/utils';
import { ModelsPage } from './ModelsPage';
import { MODEL_CARD, MODELS, MODELS_NO_WANDB, RATINGS_CARD } from './seasonFixtures';

describe('ModelsPage', () => {
  beforeEach(() => resetRefreshes());

  it('renders one card per model with versions, headline numbers, bars and settings', async () => {
    mockApi({ '/api/models': MODELS });
    const { container } = renderApp(<ModelsPage />);
    expect(await screen.findByRole('heading', { name: 'Game model' })).toBeInTheDocument();
    for (const t of ['Team ratings & Elo', 'Player models', 'Team stat totals', 'Digest writer']) {
      expect(screen.getByRole('heading', { name: t })).toBeInTheDocument();
    }
    expect(screen.getByText('production · v5')).toBeInTheDocument();
    expect(screen.getByText('game-model-v0:2026-w05')).toBeInTheDocument();
    expect(screen.getAllByText('from W&B')).toHaveLength(3);
    expect(screen.getByText('0.2199')).toBeInTheDocument();
    expect(screen.getByText('vs closing market 0.2104')).toBeInTheDocument();
    expect(screen.getByText('0.1041')).toBeInTheDocument();
    expect(screen.getByText('66.3%', { selector: '.v' })).toBeInTheDocument(); // pct is a 0–1 share
    expect(screen.getByText('23')).toBeInTheDocument();
    // the production label: mono for an artifact, plain text for ratings, not repeated for the writer
    expect(screen.getByText('game-model-v0:2026-w05')).toHaveClass('mono');
    expect(
      screen.getByText('rebuilt every Tuesday (features/team_ratings, team_elo)'),
    ).not.toHaveClass('mono');
    expect(screen.getAllByText('Production')).toHaveLength(4);
    const ratings = screen
      .getByRole('heading', { name: 'Team ratings & Elo' })
      .closest('.card') as HTMLElement;
    expect(within(ratings).queryByText(/^production/)).toBeNull();
    expect(screen.getByText('7a529f64c5a6')).toHaveClass('mono');
    expect(screen.getByRole('list', { name: /^Team stat totals: MAE/ })).toHaveTextContent(
      '+0.06%',
    );
    const mae = screen.getByRole('list', { name: /^Player models: MAE/ });
    expect(mae).toHaveTextContent('rec_yds · WR/TE');
    expect(mae).toHaveTextContent('+9.2%');
    expect(screen.getByRole('list', { name: /^Player models: Brier/ })).toHaveTextContent(
      'anytime_td · RB',
    );
    expect(screen.getByRole('list', { name: /^Team stat totals: MAE/ })).toHaveTextContent('+1.6%');
    expect(screen.getByText('z-ai/glm-5.3-flash')).toBeInTheDocument();
    expect(screen.getByText('OpenRouter · BaseTen → Novita → Relace (D88)')).toBeInTheDocument();
    const writer = screen
      .getByRole('heading', { name: 'Digest writer' })
      .closest('.card') as HTMLElement;
    expect(within(writer).getByRole('button', { name: 'Guide' })).toHaveAttribute(
      'title',
      'The digest writer',
    );
    expect(screen.getByRole('link', { name: /W&B project/ })).toHaveAttribute(
      'href',
      MODELS.wandb.project_url,
    );
    expect(container.querySelector('.wandb-banner')).toBeNull();
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('opens the model card as rendered Markdown in a dialog; Escape closes it', async () => {
    mockApi({ '/api/models': MODELS, '/api/models/card/game-model-v0': MODEL_CARD });
    renderApp(<ModelsPage />);
    const game = (await screen.findByRole('heading', { name: 'Game model' })).closest(
      '.card',
    ) as HTMLElement;
    fireEvent.click(within(game).getByRole('button', { name: 'Model card' }));
    const dialog = await screen.findByRole('dialog');
    expect(
      await within(dialog).findByRole('heading', { level: 1, name: 'Game model v0' }),
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('heading', { level: 2, name: 'Game model v0' }),
    ).toBeInTheDocument();
    expect(dialog.querySelector('.tablewrap > table')).toHaveTextContent('0.2181');
    expect(within(dialog).getByText('home win chance').tagName).toBe('STRONG');
    fireEvent.keyDown(dialog, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('opens a linked card in the dialog, keeps web links external and repo links as text', async () => {
    mockApi({
      '/api/models': MODELS,
      '/api/models/card/game-model-v0': MODEL_CARD,
      '/api/models/card/team-ratings': RATINGS_CARD,
    });
    renderApp(<ModelsPage />);
    const game = (await screen.findByRole('heading', { name: 'Game model' })).closest(
      '.card',
    ) as HTMLElement;
    fireEvent.click(within(game).getByRole('button', { name: 'Model card' }));
    const dialog = await screen.findByRole('dialog');
    await within(dialog).findByRole('heading', { level: 1, name: 'Game model v0' });
    expect(within(dialog).getByRole('link', { name: 'the W&B run' })).toHaveAttribute(
      'target',
      '_blank',
    );
    const repo = within(dialog).getByText('the design doc');
    expect(repo.tagName).toBe('SPAN');
    expect(repo).toHaveAttribute('title', 'In the repo: ../04-track1-models.md#b-game-model');
    fireEvent.click(within(dialog).getByRole('button', { name: 'the ratings card' }));
    expect(
      await within(dialog).findByRole('heading', { level: 1, name: 'Team ratings' }),
    ).toBeInTheDocument();
  });

  it('shows the top 8 bars per metric with a Show all toggle', async () => {
    const bars = Array.from({ length: 10 }, (_, i) => ({
      label: `stat_${i}`,
      value: 10 - i,
      metric: 'mae' as const,
    }));
    const player = { ...MODELS.models[2], bars };
    mockApi({ '/api/models': { ...MODELS, models: [player] } });
    renderApp(<ModelsPage />);
    const list = await screen.findByRole('list', { name: /^Player models: MAE/ });
    expect(within(list).getAllByRole('listitem')).toHaveLength(8);
    const toggle = screen.getByRole('button', { name: 'Show all 10 stats' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(
      within(screen.getByRole('list', { name: /^Player models: MAE/ })).getAllByRole('listitem'),
    ).toHaveLength(10);
    expect(screen.getByRole('button', { name: 'Show the top stats only' })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
  });

  it('labels several cards per model by title', async () => {
    mockApi({ '/api/models': MODELS });
    renderApp(<ModelsPage />);
    expect(await screen.findByRole('button', { name: 'Model card: QB' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Model card: Player model v1' })).toBeInTheDocument();
  });

  it('shows the W&B banner and still renders the local data; Try again skips the cache', async () => {
    const fetchMock = mockApi({ '/api/models': MODELS_NO_WANDB, '/api/models?refresh=1': MODELS });
    const { container } = renderApp(<ModelsPage />);
    expect(await screen.findByText("W&B isn't available")).toBeInTheDocument();
    expect(screen.getByText("WANDB_API_KEY isn't set")).toBeInTheDocument();
    expect(screen.getByText('0.2199')).toBeInTheDocument();
    expect(screen.getByText('game-model-v0:2026-w05')).toBeInTheDocument();
    expect(screen.getAllByText('from the run summary')).toHaveLength(3);
    expect(screen.queryByRole('link', { name: /W&B project/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
    await waitFor(() => expect(container.querySelector('.wandb-banner')).toBeNull());
    expect(fetchMock).toHaveBeenCalledWith('/api/models?refresh=1', expect.anything());
    expect(screen.getAllByText('from W&B')).toHaveLength(3);
  });
});
