import { type ReactNode, useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(node: HTMLElement): HTMLElement[] {
  return Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE));
}

/**
 * A modal dialog: `role="dialog"` with `aria-modal`, named by its title; focus moves into it on
 * open and Tab / Shift+Tab stay inside it; Escape closes it unless `canClose` is false (a write
 * is running). The caller puts focus back where it belongs in `onClose`.
 */
export function Dialog({
  title,
  onClose,
  canClose = true,
  children,
}: {
  title: string;
  onClose: () => void;
  canClose?: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    (focusables(node)[0] ?? node).focus();
  }, []);

  function onKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const node = ref.current;
    if (!node) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (canClose) onClose();
      return;
    }
    if (event.key !== "Tab") return;
    const list = focusables(node);
    if (list.length === 0) {
      event.preventDefault();
      node.focus();
      return;
    }
    const first = list[0];
    const last = list[list.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === node)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  return createPortal(
    <div className="dialog-backdrop">
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="dialog"
        onKeyDown={onKeyDown}
      >
        <h2 id={titleId} className="dialog__title">
          {title}
        </h2>
        {children}
      </div>
    </div>,
    document.body,
  );
}

export default Dialog;
