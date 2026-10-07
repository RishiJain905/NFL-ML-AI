import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { resetRefreshes } from '../../api/client';
import type { HealthResponse } from '../../api/types';
import { mockApi, renderApp } from '../../test/utils';
import { HealthPage } from './HealthPage';
import { HEALTH, HEALTH_CHECKING } from './seasonFixtures';

const card = (title: string) =>
  screen.getByRole('heading', { name: title }).closest('.card') as HTMLElement;

describe('HealthPage', () => {
  beforeEach(() => resetRefreshes());

  it('renders services, variables by name and the recent runs', async () => {
    mockApi({ '/api/health': HEALTH });
    const { container } = renderApp(<HealthPage />);
    expect(
      await screen.findByText('neo4j 5.26.31 (community), gds 2.13.13, apoc 5.26.31'),
    ).toBeInTheDocument();
    const services = card('Services');
    expect(within(services).getAllByText('ok')).toHaveLength(6);
    expect(within(services).getByText(/^checked /)).toBeInTheDocument();
    expect(within(services).getByRole('button', { name: 'Check again' })).toBeEnabled();
    const vars = card('Variables');
    expect(within(vars).getAllByText('set')).toHaveLength(6);
    expect(within(vars).getAllByText('not set · optional')).toHaveLength(2);
    const runs = card('Recent runs');
    expect(within(runs).getByText('nfl weekly run --auto --expect-week 5')).toHaveClass('mono');
    expect(within(runs).getByText('· via control room')).toBeInTheDocument();
    expect(within(runs).getByText('16m 29s')).toBeInTheDocument();
    expect(screen.queryByText(/problem/)).toBeNull();
    expect(container.textContent).not.toMatch(/NaN|undefined/);
  });

  it('shows only names and set / not set for variables, never anything else the answer might carry', async () => {
    const leaky = {
      ...HEALTH,
      variables: [
        { name: 'NEO4J_PASSWORD', required: true, set: true, value: 'SENTINEL-VALUE-1' },
        { name: 'WANDB_API_KEY', required: true, set: false, preview: 'SENTINEL-VALUE-2' },
      ],
    } as unknown as HealthResponse;
    mockApi({ '/api/health': leaky });
    const { container } = renderApp(<HealthPage />);
    await screen.findByText('NEO4J_PASSWORD');
    const vars = card('Variables');
    const cells = Array.from(vars.querySelectorAll('td')).map((td) => td.textContent);
    expect(cells).toEqual(['NEO4J_PASSWORD', '✓set', 'WANDB_API_KEY', '✕not set (required)']);
    expect(container).not.toHaveTextContent('SENTINEL');
    expect(screen.getByText(/1 problem/)).toBeInTheDocument();
  });

  it('says politely when a check is running and disables Check again', async () => {
    mockApi({ '/api/health': HEALTH_CHECKING });
    renderApp(<HealthPage />);
    const status = await screen.findByText(/Checking W&B, OpenRouter and Neo4j/);
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByRole('button', { name: 'Checking…' })).toBeDisabled();
    expect(within(card('Services')).getAllByText('checking')).toHaveLength(2);
    expect(within(card('Services')).getByText('not checked yet')).toBeInTheDocument();
  });

  it('Check again asks the server to skip its cache once', async () => {
    const fetchMock = mockApi({ '/api/health': HEALTH, '/api/health?refresh=1': HEALTH_CHECKING });
    renderApp(<HealthPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Check again' }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/health?refresh=1', expect.anything()),
    );
    expect(await screen.findByRole('button', { name: 'Checking…' })).toBeDisabled();
  });
});
