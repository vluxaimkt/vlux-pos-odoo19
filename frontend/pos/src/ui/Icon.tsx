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
