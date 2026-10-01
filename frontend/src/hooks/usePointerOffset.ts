import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "motion/react";

/**
 * Tracks the pointer inside one element and reports a small offset for subtle parallax.
 *
 * The listener is attached to the element, not the window, and every read is coalesced into
 * a single animation frame so pointer traffic cannot cause per-move React renders. Touch
 * devices and reduced-motion visitors get a frozen zero offset.
 */
export function usePointerOffset(maxPx = 6): {
  ref: React.RefObject<HTMLDivElement | null>;
  offset: { x: number; y: number };
} {
  const ref = useRef<HTMLDivElement | null>(null);
  const reduceMotion = useReducedMotion();
  const [offset, setOffset] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const node = ref.current;
    if (!node) return;

    const coarse = window.matchMedia("(pointer: coarse)").matches;
    if (reduceMotion || coarse) return;

    let frame = 0;
    let next = { x: 0, y: 0 };

    const flush = () => {
      frame = 0;
      setOffset((current) =>
        current.x === next.x && current.y === next.y ? current : next,
      );
    };

    const onMove = (event: PointerEvent) => {
      const rect = node.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) return;
      // Normalised -0.5..0.5 from the element centre.
      const nx = (event.clientX - rect.left) / rect.width - 0.5;
      const ny = (event.clientY - rect.top) / rect.height - 0.5;
      next = { x: nx * maxPx * 2, y: ny * maxPx * 2 };
      if (frame === 0) frame = window.requestAnimationFrame(flush);
    };

    const onLeave = () => {
      next = { x: 0, y: 0 };
      if (frame === 0) frame = window.requestAnimationFrame(flush);
    };

    node.addEventListener("pointermove", onMove);
    node.addEventListener("pointerleave", onLeave);
    return () => {
      if (frame !== 0) window.cancelAnimationFrame(frame);
      node.removeEventListener("pointermove", onMove);
      node.removeEventListener("pointerleave", onLeave);
    };
  }, [maxPx, reduceMotion]);

  return { ref, offset };
}