import { useEffect, useRef, type ReactNode } from "react";

export function DetailDrawer({
  title,
  open,
  onClose,
  children,
}: {
  title: string;
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const drawerRef = useRef<HTMLElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    returnFocus.current = document.activeElement as HTMLElement | null;
    const drawer = drawerRef.current;
    const focusable = drawer?.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    focusable?.[0]?.focus();
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
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
    drawer?.addEventListener("keydown", onKeyDown);
    return () => {
      drawer?.removeEventListener("keydown", onKeyDown);
      returnFocus.current?.focus();
    };
  }, [onClose, open]);

  if (!open) return null;
  return (
    <div
      className="detail-drawer-backdrop"
      role="presentation"
      onMouseDown={onClose}
    >
      <aside
        ref={drawerRef}
        className="detail-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="detail-drawer-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="detail-drawer__header">
          <h2 id="detail-drawer-title">{title}</h2>
          <button
            className="icon-button"
            type="button"
            onClick={onClose}
            aria-label={`Close ${title}`}
          >
            <span aria-hidden="true">×</span>
          </button>
        </header>
        <div className="detail-drawer__body">{children}</div>
      </aside>
    </div>
  );
}
