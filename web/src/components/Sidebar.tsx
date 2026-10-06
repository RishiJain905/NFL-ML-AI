// The sidebar (mockup: renderSide): brand + gear, the season's weeks with their status, the
// season and system pages, and a status footer (lock, Neo4j, data drive, clock).

import { NavLink, useNavigate, useParams } from 'react-router-dom';
import { useMeta, useWeeks } from '../api/client';
import type { WeekEntry } from '../api/types';
import { clockLabel, dayLabel } from '../lib/format';
import { useNow } from '../lib/useNow';
import { AppearancePopover } from './AppearancePopover';
import { AlertsIcon, HealthIcon, ModelsIcon, SeasonIcon, TeamsIcon } from './Icons';
import { Chip, WeekStatusChip } from './ui';

function weekSubline(w: WeekEntry, firstKickoff: string | null | undefined): string {
  if (w.is_current) return firstKickoff ? `This week · ${dayLabel(firstKickoff)}` : 'This week';
  return w.detail ?? w.label;
}

export function Sidebar() {
  const meta = useMeta();
  const weeks = useWeeks();
  const navigate = useNavigate();
  const params = useParams();
  const now = useNow();
  const current = params.week ? Number(params.week) : null;
  const data = weeks.data;
  const goLive = data?.go_live_week ?? null;
  const cal = meta.data?.calendar;
  const neo = meta.data?.neo4j;
  const lock = meta.data?.lock;

  return (
    <aside className="side" aria-label="Weeks and pages">
      <div className="brand">
        <div className="brand-mark" aria-hidden="true">
          CR
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <b>Control Room</b>
          <small>NFL Analytics Engine</small>
        </div>
        <AppearancePopover />
      </div>

      <nav className="navgrp" aria-label={`${data?.season ?? ''} weeks`}>
        <div className="eyebrow">{data ? `${data.season} weeks` : 'Weeks'}</div>
        {weeks.isLoading ? <span className="muted" style={{ padding: '0 8px' }}>Loading…</span> : null}
        {data?.weeks.map((w) => (
          <button
            key={w.week}
            type="button"
            className="navi"
            aria-current={params.season === String(w.season) && current === w.week ? 'page' : undefined}
            onClick={() => navigate(`/week/${w.season}/${w.week}/pipeline`)}
          >
            <span className="wk">{w.week}</span>
            <span className="grow">
              Week {w.week}
              <span className="sub" title={weekSubline(w, cal?.deadline)}>
                {weekSubline(w, cal?.deadline)}
              </span>
            </span>
            <WeekStatusChip status={w.status} label={w.label} short />
          </button>
        ))}
        {goLive && goLive > 1 ? (
          <button type="button" className="navi" disabled title={`Live weekly runs started in week ${goLive}`}>
            <span className="wk">{goLive === 2 ? '1' : `1–${goLive - 1}`}</span>
            <span className="grow">
              Before go-live
              <span className="sub">Walk-forward rows only</span>
            </span>
          </button>
        ) : null}
      </nav>

      <nav className="navgrp" aria-label="Season">
        <div className="eyebrow">Season</div>
        <NavLink className="navi" to={`/season/${data?.season ?? meta.data?.current_season ?? ''}/scorecard`}>
          <SeasonIcon />
          <span className="grow">Scorecard</span>
        </NavLink>
        <NavLink className="navi" to="/teams">
          <TeamsIcon />
          <span className="grow">Teams &amp; rankings</span>
        </NavLink>
        <NavLink className="navi" to="/models">
          <ModelsIcon />
          <span className="grow">Models</span>
        </NavLink>
        <NavLink className="navi" to="/alerts">
          <AlertsIcon />
          <span className="grow">Alerts</span>
        </NavLink>
      </nav>

      <nav className="navgrp" aria-label="System">
        <div className="eyebrow">System</div>
        <NavLink className="navi" to="/health">
          <HealthIcon />
          <span className="grow">Health</span>
          {neo ? (
            neo.status === 'up' ? (
              <Chip tone="ok" icon="✓">
                OK
              </Chip>
            ) : neo.status === 'checking' ? (
              <Chip tone="ghost">…</Chip>
            ) : (
              <Chip tone="warn" icon="!">
                Check
              </Chip>
            )
          ) : null}
        </NavLink>
      </nav>

      <div className="side-foot" aria-label="Status">
        <div>
          <span>Run lock</span>
          <span title={lock?.command ?? undefined}>{lock?.held ? 'held' : 'free'}</span>
        </div>
        <div>
          <span>Neo4j</span>
          <span title={neo?.reason}>{neo ? (neo.status === 'config_problem' ? 'problem' : neo.status) : '…'}</span>
        </div>
        <div>
          <span>Data drive</span>
          <span>
            {meta.data
              ? meta.data.data_root.found
                ? `${meta.data.data_root.free_gb ?? '?'} GB free`
                : 'missing'
              : '…'}
          </span>
        </div>
        <div>
          <span>Now</span>
          <span>{clockLabel(now)}</span>
        </div>
      </div>
    </aside>
  );
}
