// Line icons from the mockup's sidebar (16 px, currentColor).

const common = { width: 16, height: 16, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, 'aria-hidden': true } as const;

export const SeasonIcon = () => (
  <svg {...common}>
    <path d="M2 13.5h12M3.5 11l3-4 2.5 2.5L13 4" />
  </svg>
);
export const TeamsIcon = () => (
  <svg {...common}>
    <path d="M3 3.5h10M3 8h10M3 12.5h6" />
  </svg>
);
export const ModelsIcon = () => (
  <svg {...common}>
    <path d="M8 1.8l5.5 3.1v6.2L8 14.2l-5.5-3.1V4.9z M8 8v6.2 M8 8l5.5-3.1 M8 8L2.5 4.9" />
  </svg>
);
export const AlertsIcon = () => (
  <svg {...common}>
    <path d="M4 11V7a4 4 0 0 1 8 0v4l1 1.5H3z M6.5 14h3" />
  </svg>
);
export const HealthIcon = () => (
  <svg {...common}>
    <path d="M1.5 8.5h3l1.5-4 3 8 1.5-4h4" />
  </svg>
);
/** An O, an X and a route: the playbook mark (Explore → Play calling). */
export const PlayCallingIcon = () => (
  <svg {...common}>
    <circle cx="4" cy="12" r="2.2" />
    <path d="M10.5 10l3.5 3.5M14 10l-3.5 3.5M4 9.3C4 5.5 7 3.2 12 3.2M10 1.6l2 1.6-2 1.6" />
  </svg>
);
export const GearIcon = () => (
  <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.5} aria-hidden="true">
    <circle cx="8" cy="8" r="2.2" />
    <path d="M8 1.5v1.8M8 12.7v1.8M1.5 8h1.8M12.7 8h1.8M3.4 3.4l1.3 1.3M11.3 11.3l1.3 1.3M3.4 12.6l1.3-1.3M11.3 4.7l1.3-1.3" />
  </svg>
);
