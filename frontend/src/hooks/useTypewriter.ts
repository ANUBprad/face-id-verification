import { useEffect, useState } from "react";
import { useReducedMotion } from "motion/react";

/**
 * Reveals `text` one character at a time. When the visitor prefers reduced motion the full
 * string is returned immediately, so the headline is never withheld from anyone.
 */
export function useTypewriter(
  text: string,
  speed = 62,
  startDelay = 320,
): { displayed: string; done: boolean } {
  const reduceMotion = useReducedMotion();
  const [count, setCount] = useState(() => (reduceMotion ? text.length : 0));

  useEffect(() => {
    if (reduceMotion) {
      setCount(text.length);
      return;
    }

    setCount(0);
    let interval = 0;
    // The first character waits out startDelay; the rest arrive on the interval.
    const start = window.setTimeout(() => {
      interval = window.setInterval(() => {
        setCount((n) => {
          if (n >= text.length) {
            window.clearInterval(interval);
            return n;
          }
          return n + 1;
        });
      }, speed);
    }, startDelay);

    return () => {
      window.clearTimeout(start);
      window.clearInterval(interval);
    };
  }, [text, speed, startDelay, reduceMotion]);

  return { displayed: text.slice(0, count), done: count >= text.length };
}