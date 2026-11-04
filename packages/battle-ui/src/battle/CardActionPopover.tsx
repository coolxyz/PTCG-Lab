import { useLayoutEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";

export function CardActionPopover({
  trigger,
  name,
  onClose,
  children,
}: {
  trigger: HTMLButtonElement;
  name: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const panel = useRef<HTMLElement>(null);
  const screen = trigger.closest(".battle-screen");

  useLayoutEffect(() => {
    const element = panel.current;
    const body = trigger.closest(".mobile-battle-body");
    if (!element || !body) return;
    const gap = 8;
    const bounds = body.getBoundingClientRect();
    const card = trigger.getBoundingClientRect();
    const width = Math.min(300, bounds.width - 2 * gap);
    const above = card.top - bounds.top - 2 * gap;
    const below = bounds.bottom - card.bottom - 2 * gap;
    const left = Math.max(
      bounds.left + gap,
      Math.min(
        card.left + (card.width - width) / 2,
        bounds.right - width - gap,
      ),
    );
    element.style.width = `${width}px`;
    element.style.left = `${left}px`;
    const placeAbove = below < element.scrollHeight && above > below;
    element.style.top = placeAbove ? "auto" : `${card.bottom + gap}px`;
    element.style.bottom = placeAbove
      ? `${window.innerHeight - card.top + gap}px`
      : "auto";
    element.style.maxHeight = `${Math.max(0, placeAbove ? above : below)}px`;
    element.style.visibility = "visible";
    element.focus({ preventScroll: true });

    const closeOnOutside = (event: Event) => {
      if (
        event.target instanceof Node &&
        !trigger.contains(event.target) &&
        !element.contains(event.target)
      )
        onClose();
    };
    const closeOnScroll = (event: Event) => {
      if (!(event.target instanceof Node) || !element.contains(event.target))
        onClose();
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      trigger.focus({ preventScroll: true });
      onClose();
    };
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("focusin", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("scroll", closeOnScroll, true);
    window.addEventListener("resize", onClose);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("focusin", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("scroll", closeOnScroll, true);
      window.removeEventListener("resize", onClose);
    };
  }, [trigger, onClose]);

  return (
    screen &&
    createPortal(
      <section
        ref={panel}
        className="mobile-battle-card-actions"
        aria-label={`${name}的操作`}
        tabIndex={-1}
      >
        <strong>{name}</strong>
        {children}
      </section>,
      screen,
    )
  );
}
