/** Sign-in, sign-up and recovery: the form on one side, the brand on the other (hidden on phones). */
import { Check } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "../nav";
import { Logo } from "../ui/brand";

export function AuthLayout({
  title,
  sub,
  children,
  footer,
}: {
  title: string;
  sub?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="auth">
      <div className="auth-form-side">
        <Link href="/" className="auth-brand" aria-label="Animator home">
          <Logo size={34} />
        </Link>
        <div className="auth-card page-enter">
          <h1>{title}</h1>
          {sub && <p className="auth-sub">{sub}</p>}
          {children}
        </div>
        {footer && <div className="auth-footer">{footer}</div>}
      </div>
      <div className="auth-art" aria-hidden="true">
        <div className="auth-art-inner">
          <svg className="auth-orbit" viewBox="0 0 400 400" aria-hidden="true">
            <circle cx="200" cy="200" r="150" className="orbit-ring" />
            <circle cx="200" cy="200" r="100" className="orbit-ring" />
            <g className="orbit-spin">
              <circle cx="350" cy="200" r="12" className="orbit-dot" />
            </g>
            <g className="orbit-spin slow">
              <rect x="88" y="188" width="24" height="24" rx="6" className="orbit-dot alt" />
            </g>
            <circle cx="200" cy="200" r="44" className="orbit-core" />
            <path d="M188 180 L222 200 L188 220 Z" className="orbit-play" />
          </svg>
          <h2>Every concept, animated.</h2>
          <p>
            Type a topic. Watch it explained with moving diagrams, a friendly voice and quick checks, then ask the tutor
            anything.
          </p>
          <ul>
            <li>
              <Check size={18} /> Lessons drawn by a real animation engine
            </li>
            <li>
              <Check size={18} /> Raise your hand mid-lesson to ask a doubt
            </li>
            <li>
              <Check size={18} /> Track your progress, notes and streaks
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}
