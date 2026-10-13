/**
 * Who is signed in, for the whole app. The session lives in httpOnly cookies; this keeps the profile,
 * renews the access token before it lapses (while the app is open), and reacts when the session ends.
 */
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { type Preferences, type User, v1 } from "./api";
import { refreshSession, SESSION_ENDED } from "./http";
import { navigate } from "./nav";
import { applyTheme } from "./theme";

type Status = "loading" | "signed-in" | "signed-out";

interface AuthValue {
  user: User | null;
  status: Status;
  can: (perm: string) => boolean;
  login: (email: string, password: string, remember: boolean) => Promise<User>;
  register: (name: string, email: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  /** Replace the profile after a change (settings, onboarding). */
  setUser: (u: User) => void;
  reload: () => Promise<void>;
  prefs: Preferences | null;
}

const AuthContext = createContext<AuthValue | null>(null);
const RENEW_MS = 10 * 60 * 1000; // the access token lives 15 minutes

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<User | null>(null);
  const [status, setStatus] = useState<Status>("loading");
  const statusRef = useRef(status);
  statusRef.current = status;

  const setUser = useCallback((u: User | null) => {
    setUserState(u);
    setStatus(u ? "signed-in" : "signed-out");
    // Signed out: the brand's red again (a person's color is theirs, ADR-057).
    applyTheme(
      u?.preferences.theme ?? "light",
      u?.preferences.reduced_motion ?? false,
      u?.preferences.accent ?? "crimson",
    );
  }, []);

  const reload = useCallback(async () => {
    try {
      setUser((await v1.me()).user);
    } catch {
      setUser(null);
    }
  }, [setUser]);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Renew while open; check again when the tab comes back (a laptop waking up).
  useEffect(() => {
    if (status !== "signed-in") return;
    const renew = () => void refreshSession();
    const h = window.setInterval(renew, RENEW_MS);
    let hiddenAt = 0;
    const onVisible = () => {
      if (document.visibilityState === "hidden") hiddenAt = Date.now();
      else if (hiddenAt && Date.now() - hiddenAt > 5 * 60 * 1000) void reload();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(h);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [status, reload]);

  useEffect(() => {
    const ended = () => {
      if (statusRef.current !== "signed-in") return;
      setUser(null);
      const here = window.location.pathname + window.location.search;
      navigate(`/login?next=${encodeURIComponent(here)}&ended=1`, { replace: true });
    };
    window.addEventListener(SESSION_ENDED, ended);
    return () => window.removeEventListener(SESSION_ENDED, ended);
  }, [setUser]);

  const value = useMemo<AuthValue>(
    () => ({
      user,
      status,
      prefs: user?.preferences ?? null,
      can: (perm) => !!user?.permissions.includes(perm),
      login: async (email, password, remember) => {
        const u = (await v1.login(email, password, remember)).user;
        setUser(u);
        return u;
      },
      register: async (name, email, password) => {
        const u = (await v1.register(name, email, password)).user;
        setUser(u);
        return u;
      },
      logout: async () => {
        try {
          await v1.logout();
        } finally {
          setUser(null);
          navigate("/", { replace: true });
        }
      },
      setUser: (u) => setUser(u),
      reload,
    }),
    [user, status, setUser, reload],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const v = useContext(AuthContext);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}

/** The signed-in user (only inside pages that require one). */
export function useUser(): User {
  const { user } = useAuth();
  if (!user) throw new Error("useUser without a signed-in user");
  return user;
}
