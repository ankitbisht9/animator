/** The platform's building blocks. Styles: styles/ui.css. */
import { Eye, EyeOff, LoaderCircle, type LucideIcon, Search, X } from "lucide-react";
import {
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
  useId,
  useState,
} from "react";
import { initials } from "../format";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "soft";

export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  icon: Icon,
  iconRight: IconRight,
  block = false,
  className,
  children,
  disabled,
  type = "button",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md" | "lg";
  loading?: boolean;
  icon?: LucideIcon;
  iconRight?: LucideIcon;
  block?: boolean;
}) {
  return (
    <button
      type={type}
      className={["ui-btn", `ui-btn-${variant}`, `ui-btn-${size}`, block ? "ui-btn-block" : "", className]
        .filter(Boolean)
        .join(" ")}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <LoaderCircle className="ui-spin" size={16} /> : Icon ? <Icon size={size === "sm" ? 15 : 17} /> : null}
      {children && <span>{children}</span>}
      {IconRight && !loading && <IconRight size={size === "sm" ? 15 : 17} />}
    </button>
  );
}

export function IconButton({
  icon: Icon,
  label,
  className,
  active,
  size = 18,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: LucideIcon; label: string; active?: boolean; size?: number }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className={["ui-icon-btn", active ? "active" : "", className].filter(Boolean).join(" ")}
      {...rest}
    >
      <Icon size={size} />
    </button>
  );
}

export function Spinner({ size = 20, label = "Loading" }: { size?: number; label?: string }) {
  return <LoaderCircle className="ui-spin" size={size} aria-label={label} />;
}

export function Badge({
  tone = "neutral",
  children,
  icon: Icon,
  className,
}: {
  tone?: "neutral" | "brand" | "success" | "warning" | "danger" | "info";
  children: ReactNode;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <span className={`ui-badge ui-badge-${tone} ${className ?? ""}`}>
      {Icon && <Icon size={12} />}
      {children}
    </span>
  );
}

export function Avatar({ name, color, size = 36 }: { name: string; color?: string; size?: number }) {
  return (
    <span
      className={`ui-avatar av-${color || "crimson"}`}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.38) }}
      aria-hidden="true"
    >
      {initials(name)}
    </span>
  );
}

export function Card({
  children,
  className,
  padded = true,
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  padded?: boolean;
  as?: "div" | "section" | "article";
}) {
  return (
    <Tag className={["ui-card", padded ? "ui-card-padded" : "", className].filter(Boolean).join(" ")}>{children}</Tag>
  );
}

export function SectionTitle({ title, action, sub }: { title: string; action?: ReactNode; sub?: string }) {
  return (
    <div className="ui-section-title">
      <div>
        <h2>{title}</h2>
        {sub && <p>{sub}</p>}
      </div>
      {action}
    </div>
  );
}

export function Skeleton({
  w,
  h = 14,
  r = 8,
  className,
}: {
  w?: number | string;
  h?: number;
  r?: number;
  className?: string;
}) {
  return (
    <span className={`ui-skeleton ${className ?? ""}`} style={{ width: w ?? "100%", height: h, borderRadius: r }} />
  );
}

export function EmptyState({
  icon: Icon,
  title,
  text,
  action,
}: {
  icon: LucideIcon;
  title: string;
  text?: string;
  action?: ReactNode;
}) {
  return (
    <div className="ui-empty">
      <div className="ui-empty-icon">
        <Icon size={26} />
      </div>
      <h3>{title}</h3>
      {text && <p>{text}</p>}
      {action}
    </div>
  );
}

export function ProgressBar({
  value,
  label,
  tone = "brand",
}: {
  value: number;
  label?: string;
  tone?: "brand" | "success";
}) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div
      className={`ui-progress ui-progress-${tone}`}
      role="progressbar"
      aria-label={label ?? "Progress"}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
    >
      <span style={{ width: `${pct}%` }} />
    </div>
  );
}

export function ProgressRing({
  value,
  size = 72,
  stroke = 7,
  children,
  label,
}: {
  value: number;
  size?: number;
  stroke?: number;
  children?: ReactNode;
  label?: string;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.max(0, Math.min(1, value));
  return (
    <div
      className="ui-ring"
      style={{ width: size, height: size }}
      role="img"
      aria-label={label ?? `${Math.round(v * 100)}%`}
    >
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} className="ui-ring-track" strokeWidth={stroke} fill="none" />
        {v > 0.001 && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            className="ui-ring-value"
            strokeWidth={stroke}
            fill="none"
            strokeDasharray={c}
            strokeDashoffset={c * (1 - v)}
            strokeLinecap="round"
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          />
        )}
      </svg>
      <div className="ui-ring-center">{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------- forms

interface FieldShell {
  label?: string;
  hint?: ReactNode;
  error?: string | null;
  icon?: LucideIcon;
}

function Shell({ id, label, hint, error, children }: FieldShell & { id: string; children: ReactNode }) {
  return (
    <div className={`ui-field ${error ? "has-error" : ""}`}>
      {label && <label htmlFor={id}>{label}</label>}
      {children}
      {error ? (
        <div className="ui-field-error" id={`${id}-msg`} role="alert">
          {error}
        </div>
      ) : hint ? (
        <div className="ui-field-hint" id={`${id}-msg`}>
          {hint}
        </div>
      ) : null}
    </div>
  );
}

export function TextField({
  label,
  hint,
  error,
  icon: Icon,
  id,
  className,
  ...rest
}: FieldShell & InputHTMLAttributes<HTMLInputElement>) {
  const auto = useId();
  const fid = id ?? auto;
  return (
    <Shell id={fid} label={label} hint={hint} error={error}>
      <div className={`ui-input ${Icon ? "with-icon" : ""} ${className ?? ""}`}>
        {Icon && <Icon size={17} className="ui-input-icon" />}
        <input
          id={fid}
          aria-invalid={!!error || undefined}
          aria-describedby={error || hint ? `${fid}-msg` : undefined}
          {...rest}
        />
      </div>
    </Shell>
  );
}

export function PasswordField({
  label = "Password",
  hint,
  error,
  meter = false,
  value,
  ...rest
}: FieldShell & InputHTMLAttributes<HTMLInputElement> & { meter?: boolean }) {
  const [show, setShow] = useState(false);
  const fid = useId();
  const strength = meter ? passwordStrength(String(value ?? "")) : null;
  return (
    <Shell id={fid} label={label} hint={hint} error={error}>
      <div className="ui-input with-action">
        <input
          id={fid}
          type={show ? "text" : "password"}
          value={value}
          aria-invalid={!!error || undefined}
          aria-describedby={error || hint ? `${fid}-msg` : undefined}
          {...rest}
        />
        <button
          type="button"
          className="ui-input-action"
          onClick={() => setShow((s) => !s)}
          aria-label={show ? "Hide password" : "Show password"}
        >
          {show ? <EyeOff size={17} /> : <Eye size={17} />}
        </button>
      </div>
      {strength && String(value ?? "").length > 0 && (
        <div className={`ui-strength s${strength.score}`} aria-live="polite">
          <div className="ui-strength-bars">
            {[0, 1, 2, 3].map((i) => (
              <span key={i} className={i < strength.score ? "on" : ""} />
            ))}
          </div>
          <span>{strength.label}</span>
        </div>
      )}
    </Shell>
  );
}

/** A rough strength estimate for the meter (the server's policy decides). */
export function passwordStrength(p: string): { score: number; label: string } {
  let score = 0;
  if (p.length >= 8) score++;
  if (p.length >= 12) score++;
  if (/[a-z]/.test(p) && /[A-Z]/.test(p)) score++;
  if (/\d/.test(p) && /[^A-Za-z0-9]/.test(p)) score++;
  if (p.length < 8) score = Math.min(score, 1);
  return { score, label: ["Too short", "Weak", "Fair", "Good", "Strong"][score] ?? "Weak" };
}

export function TextArea({
  label,
  hint,
  error,
  id,
  ...rest
}: FieldShell & TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const auto = useId();
  const fid = id ?? auto;
  return (
    <Shell id={fid} label={label} hint={hint} error={error}>
      <textarea id={fid} className="ui-textarea" aria-invalid={!!error || undefined} {...rest} />
    </Shell>
  );
}

export function SelectField({
  label,
  hint,
  error,
  id,
  options,
  ...rest
}: FieldShell & SelectHTMLAttributes<HTMLSelectElement> & { options: { value: string; label: string }[] }) {
  const auto = useId();
  const fid = id ?? auto;
  return (
    <Shell id={fid} label={label} hint={hint} error={error}>
      <select id={fid} className="ui-select" {...rest}>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </Shell>
  );
}

export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="ui-switch-row">
      <div>
        <label htmlFor={id} className="ui-switch-label">
          {label}
        </label>
        {description && <div className="ui-switch-desc">{description}</div>}
      </div>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        className={`ui-switch ${checked ? "on" : ""}`}
        onClick={() => onChange(!checked)}
      >
        <span />
      </button>
    </div>
  );
}

export function Checkbox({
  checked,
  onChange,
  children,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  children: ReactNode;
}) {
  return (
    <label className="ui-check">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="ui-check-box" aria-hidden="true" />
      <span>{children}</span>
    </label>
  );
}

export function Segmented<T extends string | number>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode; disabled?: boolean }[];
  label: string;
}) {
  return (
    <fieldset className="ui-segmented">
      <legend className="sr-only">{label}</legend>
      {options.map((o) => (
        <button
          key={String(o.value)}
          type="button"
          aria-pressed={o.value === value}
          disabled={o.disabled}
          className={o.value === value ? "on" : ""}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </fieldset>
  );
}

export function Tabs<T extends string>({
  value,
  onChange,
  tabs,
  label,
}: {
  value: T;
  onChange: (v: T) => void;
  tabs: { value: T; label: string; icon?: LucideIcon; count?: number }[];
  label: string;
}) {
  return (
    <div className="ui-tabs" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button
          key={t.value}
          type="button"
          role="tab"
          aria-selected={t.value === value}
          className={t.value === value ? "on" : ""}
          onClick={() => onChange(t.value)}
        >
          {t.icon && <t.icon size={16} />}
          {t.label}
          {t.count !== undefined && <span className="ui-tab-count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

export function SearchBox({
  value,
  onChange,
  placeholder = "Search",
  label = "Search",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  label?: string;
}) {
  return (
    <div className="ui-search">
      <Search size={17} aria-hidden="true" />
      <input
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label={label}
        onChange={(e) => onChange(e.target.value)}
      />
      {value && (
        <button type="button" onClick={() => onChange("")} aria-label="Clear search">
          <X size={15} />
        </button>
      )}
    </div>
  );
}

export function Stat({
  icon: Icon,
  label,
  value,
  sub,
  tone = "brand",
}: {
  icon: LucideIcon;
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "brand" | "success" | "warning" | "info" | "neutral";
}) {
  return (
    <div className="ui-stat">
      <div className={`ui-stat-icon tone-${tone}`}>
        <Icon size={20} />
      </div>
      <div className="ui-stat-body">
        <div className="ui-stat-label">{label}</div>
        <div className="ui-stat-value">{value}</div>
        {sub && <div className="ui-stat-sub">{sub}</div>}
      </div>
    </div>
  );
}

export function Alert({
  tone = "info",
  title,
  children,
  action,
}: {
  tone?: "info" | "success" | "warning" | "danger";
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={`ui-alert ui-alert-${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <div>
        {title && <strong>{title}</strong>}
        {children && <div>{children}</div>}
      </div>
      {action}
    </div>
  );
}
