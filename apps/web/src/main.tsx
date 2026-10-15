import "@fontsource/lexend/400.css";
import "@fontsource/lexend/600.css";
import "@fontsource/lexend/700.css";
// Inter with its optical sizes: headlines take its sharper display cut by themselves (ADR-062).
import "@fontsource-variable/inter/opsz.css";
import "@fontsource-variable/source-serif-4/opsz.css";
import "@fontsource-variable/source-serif-4/opsz-italic.css";
import "./platform/styles/tokens.css";
import "./styles.css";
import "./platform/styles/ui.css";
import "./platform/styles/layout.css";
import "./platform/styles/pages.css";
import "./platform/styles/landing.css";
import "./platform/styles/lesson.css";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { installHttp } from "./platform/http";
import { restoreTheme } from "./platform/theme";

// The headless export page keeps the engine's own look; the platform starts everything else.
if (window.location.pathname !== "/render") {
  installHttp();
  restoreTheme();
}

const root = document.getElementById("root");
if (!root) throw new Error("missing #root");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
