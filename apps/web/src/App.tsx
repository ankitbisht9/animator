import { DSL_VERSION, ENGINE_VERSION } from "@animator/engine";

export function App() {
  return (
    <main style={{ fontFamily: "system-ui", padding: 32 }}>
      <h1>Animator</h1>
      <p>
        Engine {ENGINE_VERSION}, DSL {DSL_VERSION}. The player and DSL Playground arrive in Phase 2.
      </p>
    </main>
  );
}
