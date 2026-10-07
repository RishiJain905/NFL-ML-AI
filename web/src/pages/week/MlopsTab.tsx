// The MLOps tab (mockup: w4MLOps): Health · W&B runs · Artifacts. The open section is remembered
// while moving between weeks and tabs; only the open section's data is fetched.

import { useState } from 'react';
import type { MlopsSection } from '../../api/client';
import { Segmented } from '../../components/ui';
import { ArtifactsSection } from './mlops/ArtifactsSection';
import { HealthSection } from './mlops/HealthSection';
import { readSection, rememberSection } from './mlops/section';
import { WandbSection } from './mlops/WandbSection';
import './week.css';
import './mlops.css';

const SECTIONS: { value: MlopsSection; label: string; blurb: string }[] = [
  { value: 'health', label: 'Health', blurb: 'Did the run go cleanly, and was the data fresh?' },
  { value: 'wandb', label: 'W&B runs', blurb: "This week's W&B runs, their main charts redrawn here, each linked to W&B" },
  { value: 'artifacts', label: 'Artifacts', blurb: 'Every model, graph and digest version, its aliases, and where production points' },
];

export function MlopsTab({
  season,
  week,
  isCurrent,
  lastPublishedWeek,
}: {
  season: number;
  week: number;
  isCurrent: boolean;
  lastPublishedWeek: number | null;
}) {
  const [section, setSection] = useState<MlopsSection>(readSection);
  const choose = (s: MlopsSection) => {
    setSection(s);
    rememberSection(s);
  };
  return (
    <div className="wk-stack">
      <div className="sec-h">
        <Segmented
          label="MLOps sections"
          options={SECTIONS.map(({ value, label }) => ({ value, label }))}
          value={section}
          onChange={choose}
        />
        <p>{SECTIONS.find((s) => s.value === section)?.blurb}</p>
      </div>
      {section === 'health' ? (
        <HealthSection season={season} week={week} isCurrent={isCurrent} lastPublishedWeek={lastPublishedWeek} />
      ) : null}
      {section === 'wandb' ? <WandbSection season={season} week={week} /> : null}
      {section === 'artifacts' ? <ArtifactsSection season={season} week={week} /> : null}
    </div>
  );
}
