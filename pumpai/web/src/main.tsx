import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

// Number inputs, system-wide: phone keypad (inputmode) even for inputs built without it, and no value change by
// mouse wheel or arrow keys — a figure typed into a money box must stay exactly as typed.
const isNum = (el: Element | null): el is HTMLInputElement => el instanceof HTMLInputElement && el.type === "number";
const keypad = (root: ParentNode) => root.querySelectorAll('input[type="number"]:not([inputmode])').forEach((el) => el.setAttribute("inputmode", "decimal"));
keypad(document);
new MutationObserver((muts) => { for (const m of muts) for (const n of m.addedNodes) if (n instanceof HTMLElement) { if (isNum(n)) n.setAttribute("inputmode", "decimal"); else keypad(n); } })
  .observe(document.documentElement, { childList: true, subtree: true });
document.addEventListener("wheel", () => { if (isNum(document.activeElement)) document.activeElement.blur(); }, { passive: true });
document.addEventListener("keydown", (e) => { if ((e.key === "ArrowUp" || e.key === "ArrowDown") && isNum(e.target as Element)) e.preventDefault(); });

// keep the app shell available offline (production build only; dev uses Vite's server)
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => { /* not critical */ }));
}
