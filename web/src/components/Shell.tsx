import { Outlet } from 'react-router-dom';
import { useMeta } from '../api/client';
import { RehearsalBanner } from './run/RehearsalBanner';
import { RunUiProvider } from './run/RunUiProvider';
import { RunWatcher } from './run/RunWatcher';
import { Sidebar } from './Sidebar';
import { Notice } from './ui';

export function Shell() {
  const meta = useMeta();
  const m = meta.data;
  return (
    <RunUiProvider>
      <RunWatcher />
      <div className="app">
        <Sidebar />
        <main>
          {m?.mode.rehearsal ? (
            <div style={{ padding: '10px 28px 0' }}>
              <RehearsalBanner />
            </div>
          ) : null}
          {m && !m.data_root.found ? (
            <div style={{ padding: '10px 28px 0' }}>
              <Notice icon="!" tone="err">
                <b>The data drive isn&apos;t connected.</b> {m.data_root.message}
              </Notice>
            </div>
          ) : null}
          {meta.isError ? (
            <div style={{ padding: '10px 28px 0' }}>
              <Notice icon="!" tone="err">
                <b>The control room&apos;s API isn&apos;t answering.</b> Is{' '}
                <code>uv run nfl app</code> still running?
              </Notice>
            </div>
          ) : null}
          <Outlet />
        </main>
      </div>
    </RunUiProvider>
  );
}
