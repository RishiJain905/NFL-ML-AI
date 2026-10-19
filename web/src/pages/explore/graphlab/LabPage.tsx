// Explore -> Ask the Engine -> Graph lab (AE03; mockup: graphlab.js labView): the history graph's
// experiments, one tab (route) each: /explore/ask/lab/neighbours | families | chemistry |
// benchmark. Each view reads files the lab job (or the benchmark) wrote; none asks Neo4j.

import { NavLink, Navigate, useParams } from 'react-router-dom';
import { useGraphLabStatus } from '../../../api/client';
import { AskHeader, HistoryStatus } from './AskHeader';
import { BenchmarkView } from './BenchmarkView';
import { ChemistryView } from './ChemistryView';
import { FamiliesView } from './FamiliesView';
import { NeighboursView } from './NeighboursView';

const VIEWS = [
  { id: 'neighbours', label: 'Neighbours', View: NeighboursView },
  { id: 'families', label: 'Families', View: FamiliesView },
  { id: 'chemistry', label: 'Chemistry', View: ChemistryView },
  { id: 'benchmark', label: 'Benchmark', View: BenchmarkView },
] as const;

export function LabPage() {
  const { view } = useParams();
  const status = useGraphLabStatus(); // also warms the history graph up on the server
  const v = VIEWS.find((x) => x.id === view);
  if (!v) return <Navigate to="/explore/ask/lab/neighbours" replace />;
  const { View } = v;
  return (
    <>
      <AskHeader status={<HistoryStatus st={status.data} />} />
      <section className="view">
        <div className="gl-stack">
          <nav className="gl-tabs" aria-label="Graph lab">
            {VIEWS.map((x) => (
              <NavLink key={x.id} to={`/explore/ask/lab/${x.id}`}>
                {x.label}
              </NavLink>
            ))}
          </nav>
          <View key={v.id} />
        </div>
      </section>
    </>
  );
}
