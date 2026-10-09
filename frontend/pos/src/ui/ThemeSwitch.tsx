import { useState } from "preact/hooks";

import { readTheme, saveTheme, type ThemeChoice } from "../lib/theme";
import { Icon, type IconName } from "./Icon";

const OPTIONS: { value: ThemeChoice; label: string; icon: IconName }[] = [
  { value: "light", label: "Claro", icon: "sun" },
  { value: "dark", label: "Oscuro", icon: "moon" },
  { value: "system", label: "Automático (como el sistema)", icon: "auto" },
];

/** Light / dark / the system's, for this device: a small segmented control of icons. */
export function ThemeSwitch({ class: className = "" }: { class?: string }) {
  const [choice, setChoice] = useState<ThemeChoice>(() => readTheme());
  return (
    <div class={`segmented !p-0.5 ${className}`} role="radiogroup" aria-label="Tema">
      {OPTIONS.map((option) => (
        <button key={option.value} type="button" role="radio" aria-checked={choice === option.value}
          aria-selected={choice === option.value} aria-label={option.label} title={option.label}
          class="!min-h-8 !px-3 grid place-items-center"
          onClick={() => { setChoice(option.value); saveTheme(option.value); }}>
          <Icon name={option.icon} size={16} />
        </button>
      ))}
    </div>
  );
}
