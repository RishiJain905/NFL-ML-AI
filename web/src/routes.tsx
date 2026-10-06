import type { RouteObject } from 'react-router-dom';
import { Shell } from './components/Shell';
import { HomeRedirect, WeekRedirect } from './pages/Redirects';
import { WeekPage } from './pages/WeekPage';
import { AlertsPage, HealthPage, ModelsPage, NotFound, SeasonPage, TeamsPage } from './pages/Pages';

export const routes: RouteObject[] = [
  {
    element: <Shell />,
    children: [
      { index: true, element: <HomeRedirect /> },
      { path: 'week/:season/:week', element: <WeekRedirect /> },
      { path: 'week/:season/:week/:tab', element: <WeekPage /> },
      { path: 'season/:season/scorecard', element: <SeasonPage /> },
      { path: 'teams', element: <TeamsPage /> },
      { path: 'models', element: <ModelsPage /> },
      { path: 'alerts', element: <AlertsPage /> },
      { path: 'health', element: <HealthPage /> },
      { path: '*', element: <NotFound /> },
    ],
  },
];
