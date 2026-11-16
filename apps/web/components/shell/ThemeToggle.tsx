/*
 * The theme control: the one place the dashboard writes the theme cookie and
 * the `data-theme` attribute the server already rendered from it.
 */
"use client";

import { useCallback, useSyncExternalStore } from "react";
import type { ReactElement } from "react";

import { parseTheme, THEME_COOKIE, type ThemeChoice } from "@/lib/theme";

import styles from "./shell.module.css";

const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365;

function labelFor(choice: ThemeChoice | null): string {
  if (choice === "light") {
    return "Light";
  }
  return choice === "dark" ? "Dark" : "System";
}

/** The stored choice as the document currently declares it, or `null`. */
function readChoice(): ThemeChoice | null {
  return parseTheme(document.documentElement.dataset.theme);
}

/** Follow the attribute, so a change made elsewhere is picked up as well. */
function subscribe(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  return () => {
    observer.disconnect();
  };
}

// The server render is also the pre-hydration client render, so it must not
// read the document: it reports "no choice" and the attribute is read after
// hydration, which is exactly the value the server already wrote into the HTML.
const serverChoice = (): ThemeChoice | null => null;

const serverNotMounted = (): boolean => false;
const clientMounted = (): boolean => true;
const noSubscription = (): (() => void) => () => {
  /* Nothing to unsubscribe: being mounted does not change again. */
};

/**
 * The control that cycles the theme between the system and the two fixed ones.
 *
 * The attribute is the source of truth, not component state: the layout wrote
 * it from the cookie on the server, so the first paint is already correct and
 * this control only reports what is there. Reading it through
 * `useSyncExternalStore` keeps the read out of render, so the server and the
 * browser agree, and lets the same click handler serve the cookie and the DOM
 * without a second copy of the choice.
 */
export function ThemeToggle(): ReactElement {
  const choice = useSyncExternalStore(subscribe, readChoice, serverChoice);
  const mounted = useSyncExternalStore(
    noSubscription,
    clientMounted,
    serverNotMounted,
  );

  const cycle = useCallback((): void => {
    // system -> light -> dark -> system: three presses return the reader to the
    // system preference, so the cycle has no dead end.
    const next: ThemeChoice | null =
      choice === null ? "light" : choice === "light" ? "dark" : null;
    const root = document.documentElement;

    if (next === null) {
      // Max-Age=0 deletes the cookie: following the system is the absence of a
      // choice, not a third stored value.
      document.cookie = `${THEME_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax`;
      delete root.dataset.theme;
    } else {
      document.cookie = `${THEME_COOKIE}=${next}; Path=/; Max-Age=${COOKIE_MAX_AGE_SECONDS}; SameSite=Lax`;
      root.dataset.theme = next;
    }
  }, [choice]);

  return (
    <button
      aria-label={mounted ? `Theme: ${choice ?? "system"}` : "Theme"}
      className={styles.themeToggle}
      onClick={cycle}
      type="button"
    >
      {labelFor(choice)}
    </button>
  );
}
