import type { JSX } from "preact";

/**
 * Line icons in the spirit of SF Symbols: 24 px grid, 1.75 stroke, round caps.
 * One name per module and a few for chrome; a new icon is a new entry here.
 */
const PATHS = {
  cart: <><circle cx="9" cy="20" r="1.5" /><circle cx="18" cy="20" r="1.5" /><path d="M3 4h2.2l2.1 10.2a1.5 1.5 0 0 0 1.5 1.2h8.4a1.5 1.5 0 0 0 1.5-1.1L20.5 8H6" /></>,
  receipt: <><path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z" /><path d="M9.5 8h5M9.5 12h5" /></>,
  people: <><circle cx="9" cy="8.5" r="3.25" /><path d="M3 20c.4-3.3 2.7-5 6-5s5.6 1.7 6 5" /><path d="M16 5.6a3.25 3.25 0 0 1 0 5.8M18 15.3c1.7.6 2.7 2 3 4.7" /></>,
  cash: <><rect x="3" y="6" width="18" height="12" rx="3" /><circle cx="12" cy="12" r="2.5" /><path d="M6.5 12h.01M17.5 12h.01" /></>,
  calculator: <><rect x="5" y="3" width="14" height="18" rx="3" /><path d="M8.5 7.5h7M8.5 12h.01M12 12h.01M15.5 12h.01M8.5 16h.01M12 16h.01M15.5 16h.01" /></>,
  idcard: <><rect x="3" y="5" width="18" height="14" rx="3" /><circle cx="9" cy="11" r="2" /><path d="M5.8 16c.5-1.6 1.7-2.4 3.2-2.4s2.7.8 3.2 2.4M14.5 10h3.5M14.5 13.5H18" /></>,
  crown: <><path d="M4 8.5 8 12l4-6.5L16 12l4-3.5-1.4 9.5H5.4L4 8.5Z" /><path d="M5.5 20.5h13" /></>,
  tray: <><path d="M12 3v10M8 9l4 4 4-4" /><path d="M3.5 13.5V17a3 3 0 0 0 3 3h11a3 3 0 0 0 3-3v-3.5h-5l-1.2 2h-5.6l-1.2-2h-5Z" /></>,
  lock: <><rect x="5" y="10.5" width="14" height="10" rx="3" /><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5" /></>,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  backspace: <><path d="M21 6.5A1.5 1.5 0 0 0 19.5 5H9L3 12l6 7h10.5a1.5 1.5 0 0 0 1.5-1.5v-11Z" /><path d="m12.5 9.5 5 5M17.5 9.5l-5 5" /></>,
  chevron: <path d="m9 6 6 6-6 6" />,
  back: <path d="m15 6-6 6 6 6" />,
  power: <><path d="M12 3v8" /><path d="M7 6.3a8 8 0 1 0 10 0" /></>,
  phone: <><rect x="7" y="2.5" width="10" height="19" rx="2.5" /><path d="M11 18.5h2" /></>,
  camera: <><path d="M4 8.5A1.5 1.5 0 0 1 5.5 7h2.3l1.5-2h5.4l1.5 2h2.3A1.5 1.5 0 0 1 20 8.5v9a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 17.5v-9Z" /><circle cx="12" cy="13" r="3.25" /></>,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  warning: <><path d="M10.3 4.2a2 2 0 0 1 3.4 0l7.5 13A2 2 0 0 1 19.5 20h-15a2 2 0 0 1-1.7-2.8l7.5-13Z" /><path d="M12 9.5v4M12 16.5h.01" /></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m15.5 15.5 5 5" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v5.5M12 7.5h.01" /></>,
  card: <><rect x="3" y="5.5" width="18" height="13" rx="3" /><path d="M3 10h18M7 15h3" /></>,
  person: <><circle cx="12" cy="8.5" r="3.75" /><path d="M5 20c.6-3.6 3.3-5.5 7-5.5s6.4 1.9 7 5.5" /></>,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  scale: <><path d="M5 20h14l-1.6-9.2A2 2 0 0 0 15.4 9H8.6a2 2 0 0 0-2 1.8L5 20Z" /><circle cx="12" cy="5.5" r="2.5" /><path d="m12 13.5 1.8-1.8" /></>,
  printer: <><path d="M7 9V3.5h10V9" /><rect x="3.5" y="9" width="17" height="8" rx="2.5" /><path d="M7 14.5h10v6H7z" /></>,
  trash: <path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l.9 12.1a1.5 1.5 0 0 0 1.5 1.4h6.2a1.5 1.5 0 0 0 1.5-1.4L17.5 7" />,
  checkCircle: <><circle cx="12" cy="12" r="9" /><path d="m8 12.5 2.8 2.8L16.5 9.5" /></>,
  cloudOff: <><path d="M3 3l18 18" /><path d="M8.5 7.4A6 6 0 0 1 17.6 11a4 4 0 0 1 2.6 6.4M16 18H7a4.5 4.5 0 0 1-1.8-8.6" /></>,
  arrowUp: <path d="M12 19V5M6 11l6-6 6 6" />,
  arrowDown: <path d="M12 5v14M6 13l6 6 6-6" />,
  refresh: <><path d="M20 12a8 8 0 1 1-2.3-5.7" /><path d="M20 4v4.5h-4.5" /></>,
  link: <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3A4 4 0 0 0 11 18.7l1-1" />,
} satisfies Record<string, JSX.Element>;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 24, class: className = "" }: { name: IconName; size?: number; class?: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="1.75"
      stroke-linecap="round"
      stroke-linejoin="round"
      class={`shrink-0 ${className}`}
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}
