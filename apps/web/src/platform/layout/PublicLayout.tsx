/** Pages for everyone (landing, Explore and shared lessons when signed out, help, legal): a top bar
 * with sign-in and a footer. */
import { Menu as MenuIcon, X } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { useAuth } from "../auth";
import { Link, navigate, useLocation } from "../nav";
import { BRAND, Logo } from "../ui/brand";
import { Button } from "../ui/core";

export function PublicLayout({ children, bare = false }: { children: ReactNode; bare?: boolean }) {
  const { status } = useAuth();
  const { path } = useLocation();
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset when these change
  useEffect(() => setOpen(false), [path]);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 8);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  const signedIn = status === "signed-in";
  return (
    <div className="public">
      <header className={`public-bar ${scrolled ? "scrolled" : ""} ${open ? "open" : ""}`}>
        <div className="public-bar-inner">
          <Link href="/" className="public-brand" aria-label={`${BRAND} home`}>
            <Logo size={32} />
          </Link>
          <nav className="public-nav" aria-label="Site">
            <a href="/#features">Features</a>
            <a href="/#how">How it works</a>
            <Link href="/explore" activeClassName="active">
              Explore
            </Link>
            <Link href="/help" activeClassName="active">
              Help
            </Link>
          </nav>
          <div className="public-cta">
            {signedIn ? (
              <Button variant="primary" onClick={() => navigate("/dashboard")}>
                Go to dashboard
              </Button>
            ) : (
              <>
                <Link href="/login" className="public-signin">
                  Sign in
                </Link>
                <Button variant="primary" onClick={() => navigate("/signup")}>
                  Get started free
                </Button>
              </>
            )}
          </div>
          <button
            type="button"
            className="ui-icon-btn public-menu"
            aria-label={open ? "Close menu" : "Open menu"}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? <X size={20} /> : <MenuIcon size={20} />}
          </button>
        </div>
      </header>
      <main className="public-main page-enter" key={path}>
        {children}
      </main>
      {!bare && <Footer />}
    </div>
  );
}

function Footer() {
  return (
    <footer className="public-footer">
      <div className="public-footer-inner">
        <div className="public-footer-brand">
          <Logo size={28} />
          <p>Animated lessons for any topic, made in minutes, explained like a great teacher would.</p>
        </div>
        <div className="public-footer-cols">
          <div>
            <h4>Product</h4>
            <Link href="/signup">Create a lesson</Link>
            <Link href="/explore">Explore lessons</Link>
            <Link href="/samples/archimedes">Watch a sample</Link>
          </div>
          <div>
            <h4>Help</h4>
            <Link href="/help">Help center</Link>
            <Link href="/help#shortcuts">Keyboard shortcuts</Link>
            <Link href="/support">Contact support</Link>
          </div>
          <div>
            <h4>Legal</h4>
            <Link href="/terms">Terms of use</Link>
            <Link href="/privacy">Privacy policy</Link>
          </div>
        </div>
      </div>
      <div className="public-footer-base">
        © {new Date().getFullYear()} {BRAND}. All rights reserved.
      </div>
    </footer>
  );
}
