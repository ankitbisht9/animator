/** Dialogs, menus and toasts. Dialogs trap focus, close on Escape, and give focus back when closed. */
import { CircleCheck, CircleX, Info, type LucideIcon, TriangleAlert, X } from "lucide-react";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import { useDismiss } from "../hooks";
import { Button } from "./core";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

export function Modal({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
}) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const before = document.activeElement as HTMLElement | null;
    const box = ref.current;
    const first = box?.querySelector<HTMLElement>("[data-autofocus]") ?? box?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current();
      } else if (e.key === "Tab" && box) {
        const items = [...box.querySelectorAll<HTMLElement>(FOCUSABLE)];
        if (!items.length) return;
        const firstEl = items[0];
        const lastEl = items.at(-1);
        if (e.shiftKey && document.activeElement === firstEl) {
          e.preventDefault();
          lastEl?.focus();
        } else if (!e.shiftKey && document.activeElement === lastEl) {
          e.preventDefault();
          firstEl?.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = overflow;
      before?.focus?.();
    };
  }, [open]);
  if (!open) return null;
  return createPortal(
    <div className="ui-modal-layer">
      <button type="button" className="ui-modal-backdrop" aria-label="Close" tabIndex={-1} onClick={onClose} />
      <div
        ref={ref}
        className={`ui-modal ui-modal-${size}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-t`}
      >
        <div className="ui-modal-head">
          <div>
            <h2 id={`${id}-t`}>{title}</h2>
            {description && <div className="ui-modal-desc">{description}</div>}
          </div>
          <button type="button" className="ui-icon-btn" onClick={onClose} aria-label="Close">
            <X size={18} />
          </button>
        </div>
        {children && <div className="ui-modal-body">{children}</div>}
        {footer && <div className="ui-modal-foot">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}

export function Confirm({
  open,
  onClose,
  onConfirm,
  title,
  text,
  confirmLabel = "Confirm",
  danger = false,
  busy = false,
  children,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  text?: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
  children?: ReactNode;
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      description={text}
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant={danger ? "danger" : "primary"} loading={busy} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {children}
    </Modal>
  );
}

export interface MenuItem {
  label: string;
  icon?: LucideIcon;
  onSelect: () => void;
  danger?: boolean;
  hidden?: boolean;
}

export function Menu({
  trigger,
  items,
  align = "end",
  label,
}: {
  trigger: (props: { onClick: () => void; "aria-expanded": boolean; "aria-haspopup": "menu" }) => ReactNode;
  items: MenuItem[];
  align?: "start" | "end";
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useDismiss(ref, open, close);
  const shown = items.filter((i) => !i.hidden);
  return (
    <div className="ui-menu-wrap" ref={ref}>
      {trigger({ onClick: () => setOpen((o) => !o), "aria-expanded": open, "aria-haspopup": "menu" })}
      {open && (
        <div className={`ui-menu ui-menu-${align}`} role="menu" aria-label={label}>
          {shown.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              className={item.danger ? "danger" : ""}
              onClick={() => {
                setOpen(false);
                item.onSelect();
              }}
            >
              {item.icon && <item.icon size={16} />}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------- toasts

type Tone = "success" | "error" | "info" | "warning";
interface ToastItem {
  id: number;
  tone: Tone;
  text: string;
  action?: { label: string; run: () => void };
}
interface ToastApi {
  show: (tone: Tone, text: string, action?: ToastItem["action"]) => void;
  success: (text: string) => void;
  error: (text: string) => void;
  info: (text: string, action?: ToastItem["action"]) => void;
}

const ToastContext = createContext<ToastApi | null>(null);
const ICONS: Record<Tone, LucideIcon> = { success: CircleCheck, error: CircleX, info: Info, warning: TriangleAlert };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const next = useRef(1);
  const remove = useCallback((id: number) => setItems((all) => all.filter((t) => t.id !== id)), []);
  const show = useCallback(
    (tone: Tone, text: string, action?: ToastItem["action"]) => {
      const id = next.current++;
      setItems((all) => [...all.slice(-3), { id, tone, text, action }]);
      window.setTimeout(() => remove(id), tone === "error" ? 7000 : 4500);
    },
    [remove],
  );
  const api = useMemo<ToastApi>(
    () => ({
      show,
      success: (t) => show("success", t),
      error: (t) => show("error", t),
      info: (t, a) => show("info", t, a),
    }),
    [show],
  );
  return (
    <ToastContext.Provider value={api}>
      {children}
      {createPortal(
        <div className="ui-toasts" aria-live="polite">
          {items.map((t) => {
            const Icon = ICONS[t.tone];
            return (
              <div key={t.id} className={`ui-toast ui-toast-${t.tone}`} role={t.tone === "error" ? "alert" : "status"}>
                <Icon size={18} />
                <span>{t.text}</span>
                {t.action && (
                  <button
                    type="button"
                    className="ui-toast-action"
                    onClick={() => {
                      t.action?.run();
                      remove(t.id);
                    }}
                  >
                    {t.action.label}
                  </button>
                )}
                <button type="button" className="ui-toast-close" onClick={() => remove(t.id)} aria-label="Dismiss">
                  <X size={15} />
                </button>
              </div>
            );
          })}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastApi {
  const t = useContext(ToastContext);
  if (!t) throw new Error("useToast outside ToastProvider");
  return t;
}
