import type { RouteObject } from 'react-router-dom';
import { Shell } from './components/Shell';
import { HomeRedirect, WeekRedirect } from './pages/Redirects';
import { WeekPage } from './pages/WeekPage';
import { NotFound } from './pages/Pages';
import { AskPage } from './pages/explore/AskPage';
import { ConnectionsPage } from './pages/explore/graphlab/ConnectionsPage';
import { LabPage } from './pages/explore/graphlab/LabPage';
import { PlayCallTeamPage } from './pages/explore/PlayCallTeamPage';
import { PlayCallingPage } from './pages/explore/PlayCallingPage';
import { AlertsPage } from './pages/season/AlertsPage';
import { HealthPage } from './pages/season/HealthPage';
import { ModelsPage } from './pages/season/ModelsPage';
import { ScorecardPage } from './pages/season/ScorecardPage';
import { TeamsPage } from './pages/season/TeamsPage';

export const routes: RouteObject[] = [
  {
    element: <Shell />,
    children: [
      { index: true, element: <HomeRedirect /> },
      { path: 'week/:season/:week', element: <WeekRedirect /> },
      { path: 'week/:season/:week/:tab', element: <WeekPage /> },
      { path: 'season/:season/scorecard', element: <ScorecardPage /> },
      { path: 'teams', element: <TeamsPage /> },
      { path: 'models', element: <ModelsPage /> },
      { path: 'alerts', element: <AlertsPage /> },
      { path: 'health', element: <HealthPage /> },
      { path: 'explore/play-calling', element: <PlayCallingPage /> },
      { path: 'explore/play-calling/:team', element: <PlayCallTeamPage /> },
      { path: 'explore/ask', element: <AskPage /> },
      { path: 'explore/ask/connections', element: <ConnectionsPage /> },
      { path: 'explore/ask/lab', element: <LabPage /> },
      { path: 'explore/ask/lab/:view', element: <LabPage /> },
      { path: '*', element: <NotFound /> },
    ],
  },
];
