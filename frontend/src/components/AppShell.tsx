import { useEffect, useRef, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";

import { TaskSidebar } from "./TaskSidebar";

const SIDEBAR_PREFERENCE = "enterprise-copilot:sidebar-collapsed";

export function AppShell() {
  const location = useLocation();
  const dialogRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(
    () => window.localStorage.getItem(SIDEBAR_PREFERENCE) === "true",
  );

  useEffect(() => setMobileOpen(false), [location.pathname]);

  useEffect(() => {
    if (!mobileOpen) return;
    const dialog = dialogRef.current;
    const focusable = dialog?.querySelectorAll<HTMLElement>(
      'a[href], button:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    focusable?.[0]?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMobileOpen(false);
        menuButtonRef.current?.focus();
        return;
      }
      if (event.key !== "Tab" || !focusable?.length) return;
      const first = focusable.item(0);
      const last = focusable.item(focusable.length - 1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
    dialog?.addEventListener("keydown", onKeyDown);
    return () => dialog?.removeEventListener("keydown", onKeyDown);
  }, [mobileOpen]);

  function toggleCollapsed() {
    const next = !collapsed;
    setCollapsed(next);
    window.localStorage.setItem(SIDEBAR_PREFERENCE, String(next));
  }

  return (
    <div
      className={`workspace-shell ${collapsed ? "workspace-shell--collapsed" : ""}`}
    >
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <div className="desktop-sidebar">
        <TaskSidebar
          collapsed={collapsed}
          mobile={false}
          closeMobile={() => undefined}
          toggleCollapsed={toggleCollapsed}
        />
      </div>
      <button
        ref={menuButtonRef}
        className="mobile-menu-button icon-button"
        type="button"
        aria-label="Open task history"
        aria-expanded={mobileOpen}
        onClick={() => setMobileOpen(true)}
      >
        <span aria-hidden="true">☰</span>
      </button>
      {mobileOpen && (
        <div
          className="mobile-sidebar-backdrop"
          role="presentation"
          onMouseDown={() => setMobileOpen(false)}
        >
          <div
            ref={dialogRef}
            className="mobile-sidebar-dialog"
            role="dialog"
            aria-modal="true"
            aria-label="Task history"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <TaskSidebar
              collapsed={false}
              mobile
              closeMobile={() => setMobileOpen(false)}
              toggleCollapsed={() => setMobileOpen(false)}
            />
          </div>
        </div>
      )}
      <main id="main-content" className="workspace-main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
