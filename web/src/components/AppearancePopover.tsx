// The gear next to "Control Room": theme (Turf & Pylon / Playbook) and mode
// (System / Dark / Light), saved in this browser (D96).

import * as Popover from '@radix-ui/react-popover';
import { MODES, PALETTES, type Mode } from '../theme/appearance';
import { useAppearance } from '../theme/AppearanceProvider';
import { GearIcon } from './Icons';
import { Segmented } from './ui';

const MODE_LABEL: Record<Mode, string> = { system: 'System', dark: 'Dark', light: 'Light' };

export function AppearancePopover() {
  const { appearance, setAppearance } = useAppearance();
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button type="button" className="iconbtn" aria-label="Appearance" title="Appearance">
          <GearIcon />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          className="popover"
          style={{ position: 'static' }}
          side="bottom"
          align="end"
          sideOffset={8}
          collisionPadding={16}
          aria-label="Appearance"
        >
          <span className="eyebrow">Theme</span>
          <div className="themes">
            {PALETTES.map((p) => (
              <button
                key={p.id}
                type="button"
                className="themecard"
                aria-pressed={appearance.palette === p.id}
                onClick={() => setAppearance({ ...appearance, palette: p.id })}
              >
                <i aria-hidden="true">
                  {p.swatch.map((c) => (
                    <b key={c} style={{ background: c }} />
                  ))}
                </i>
                <span>
                  <b>{p.name}</b>
                  <small>{p.note}</small>
                </span>
              </button>
            ))}
          </div>
          <span className="eyebrow">Mode</span>
          <Segmented
            label="Mode"
            options={MODES.map((m) => ({ value: m, label: MODE_LABEL[m] }))}
            value={appearance.mode}
            onChange={(mode) => setAppearance({ ...appearance, mode })}
          />
          <span className="muted" style={{ fontSize: 11.5 }}>
            Saved in this browser. Each week&apos;s Pipeline tab has its own view picker.
          </span>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
