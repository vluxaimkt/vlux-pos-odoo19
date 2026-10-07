import type { ComponentChildren } from "preact";

import { Icon, type IconName } from "./Icon";

/**
 * Shared pieces of the register's screens, in the iOS idiom: a large title
 * with a back button, a segmented control, stat cards, grouped sections,
 * labelled fields and empty states. Screens compose these instead of styling
 * each part again.
 */

/** Large title, optional icon and subtitle; back button on the left, actions on the right. */
export function PageHeader({ title, subtitle, icon, back, backLabel = "Vender", actions }: {
  title: ComponentChildren;
  subtitle?: ComponentChildren;
  icon?: IconName;
  back?: () => void;
  backLabel?: string;
  actions?: ComponentChildren;
}) {
  return (
    <header class="flex flex-col gap-2">
      {(back || actions) && (
        <div class="flex items-center gap-2 min-h-11">
          {back && (
            <button type="button" class="btn btn-ghost text-primary -ml-3 px-3" onClick={back}>
              <Icon name="back" size={20} /> {backLabel}
            </button>
          )}
          <div class="flex-1" />
          {actions}
        </div>
      )}
      <div class="flex items-center gap-3">
        {icon && (
          <span class="avatar-disc w-11 h-11 shrink-0" data-role="manager" aria-hidden="true"><Icon name={icon} size={24} /></span>
        )}
        <div class="min-w-0">
          <h1 class="text-3xl truncate">{title}</h1>
          {subtitle && <div class="label-2 text-sm">{subtitle}</div>}
        </div>
      </div>
    </header>
  );
}

/** iOS segmented control (tabs). */
export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div class="segmented" role="tablist" aria-label={label}>
      {options.map((option) => (
        <button key={option.value} type="button" role="tab" aria-selected={value === option.value}
          onClick={() => onChange(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** A figure with its caption (sales, expected cash…). */
export function Stat({ label, value, note, tone }: {
  label: string;
  value: ComponentChildren;
  note?: ComponentChildren;
  tone?: "good" | "bad" | "warn";
}) {
  const color = tone === "good" ? "text-success" : tone === "bad" ? "text-danger" : tone === "warn" ? "pill-warn" : "";
  return (
    <div class="surface p-4 flex flex-col gap-1 min-w-0">
      <span class="text-sm label-2 font-medium">{label}</span>
      <span class={`text-2xl font-semibold num truncate ${color}`}>{value}</span>
      {note && <span class="text-xs label-2">{note}</span>}
    </div>
  );
}

/** A titled group of rows (Settings style). */
export function Section({ title, footer, children, class: className = "" }: {
  title?: ComponentChildren;
  footer?: ComponentChildren;
  children: ComponentChildren;
  class?: string;
}) {
  return (
    <section class={`flex flex-col gap-2 min-w-0 ${className}`}>
      {title && <h2 class="text-sm label-2 font-semibold uppercase tracking-wide px-4">{title}</h2>}
      <div class="grouped">{children}</div>
      {footer && <p class="text-xs label-2 px-4">{footer}</p>}
    </section>
  );
}

/** A label above its control, with an optional hint below. */
export function Field({ label, hint, children }: { label: string; hint?: ComponentChildren; children: ComponentChildren }) {
  return (
    <label class="flex flex-col gap-2">
      <span class="text-sm label-2 font-medium px-1">{label}</span>
      {children}
      {hint && <span class="text-xs label-2 px-1">{hint}</span>}
    </label>
  );
}

/** Nothing to show yet: an icon, a line and a hint. */
export function EmptyState({ icon, title, hint }: { icon: IconName; title: string; hint?: string }) {
  return (
    <div class="py-16 px-4 flex flex-col items-center gap-2 text-center label-2">
      <Icon name={icon} size={40} />
      <p class="font-semibold text-base-content">{title}</p>
      {hint && <p class="text-sm">{hint}</p>}
    </div>
  );
}

/** A banner in the flow of the page (not a toast): warning, error or success, with optional actions. */
export function Banner({ tone, children, actions }: { tone: "warn" | "error" | "ok"; children: ComponentChildren; actions?: ComponentChildren }) {
  const icon: IconName = tone === "ok" ? "checkCircle" : "warning";
  const color = tone === "ok" ? "text-success" : tone === "error" ? "text-danger" : "text-warning";
  return (
    <div role={tone === "ok" ? "status" : "alert"} class="surface rise px-4 py-3 flex items-start gap-3">
      <Icon name={icon} class={`${color} mt-0.5`} />
      <div class="flex-1 min-w-0 flex flex-col gap-2">
        <div>{children}</div>
        {actions && <div class="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}

/** The paper slip on screen (ticket, voucher): white, rounded, lifted; plain on paper. */
export const SLIP = "receipt bg-white text-black font-mono text-sm p-4 w-[80mm] max-w-full rounded-[12px] shadow-lg print:shadow-none print:rounded-none rise";

/** Above a slip: a check that pops, the title and a line of detail. */
export function SuccessHeader({ title, children, tone = "good", icon = "check" }: {
  title: string;
  children?: ComponentChildren;
  tone?: "good" | "warn";
  icon?: IconName;
}) {
  const background = tone === "good" ? "linear-gradient(180deg, #34c759, #248a3d)" : "linear-gradient(180deg, #ffb340, #ff9500)";
  return (
    <header class="rise flex flex-col items-center gap-2 text-center print:hidden">
      <span class="avatar-disc w-16 h-16 pop" style={{ background }}><Icon name={icon} size={36} /></span>
      <h1 class="text-3xl">{title}</h1>
      {children && <div class="label-2">{children}</div>}
    </header>
  );
}

/** Below a slip: print it, then go on. */
export function SlipActions({ onDone, doneLabel = "Listo", printLabel = "Imprimir" }: {
  onDone: () => void;
  doneLabel?: string;
  printLabel?: string;
}) {
  return (
    <div class="flex flex-wrap justify-center gap-4 print:hidden w-full max-w-md">
      <button class="btn btn-xl flex-1 bg-base-100 border-[0.5px] border-[var(--glass-border)]" onClick={() => window.print()}>
        <Icon name="printer" size={20} /> {printLabel}
      </button>
      <button class="btn btn-primary btn-xl flex-1" autofocus onClick={onDone}>{doneLabel}</button>
    </div>
  );
}

/** Loading, centered. */
export function Loading() {
  return <div class="py-16 grid place-items-center"><span class="loading loading-spinner loading-lg text-primary" /></div>;
}
