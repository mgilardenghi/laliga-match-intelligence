/**
 * The outermost boundary: the last screen left when even the layout failed.
 *
 * Next renders this file instead of the root layout, so it owns the document —
 * `html` and `body` — and carries its own layout as inline styles, because the
 * app stylesheet is not guaranteed to be part of this response. The colours are
 * the tokens with their light values as fallbacks, which keeps the screen
 * readable when the tokens themselves never load.
 */
"use client";

import type { CSSProperties, ReactElement } from "react";

export type GlobalErrorProps = {
  readonly error: Error & { readonly digest?: string };
  readonly reset: () => void;
};

const BODY_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "flex-start",
  gap: "0.75rem",
  minHeight: "100vh",
  maxWidth: "42rem",
  margin: "0 auto",
  padding: "3rem 1rem",
  color: "var(--text, #171717)",
  background: "var(--background, #ffffff)",
  fontFamily:
    'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
  lineHeight: 1.5,
};

const TITLE_STYLE: CSSProperties = {
  fontSize: "1.75rem",
  fontWeight: 700,
  lineHeight: 1.2,
};

const TEXT_STYLE: CSSProperties = {
  color: "var(--muted, #5f6368)",
};

const BUTTON_STYLE: CSSProperties = {
  padding: "0.45rem 0.9rem",
  border: "1px solid var(--control-border, #6b7280)",
  borderRadius: "0.4rem",
  background: "var(--background, #ffffff)",
  color: "var(--text, #171717)",
  font: "inherit",
  fontWeight: 600,
  cursor: "pointer",
};

export default function GlobalError(props: GlobalErrorProps): ReactElement {
  const { reset } = props;

  return (
    <html lang="en">
      <body style={BODY_STYLE}>
        <h1 style={TITLE_STYLE}>API unreachable</h1>
        <p style={TEXT_STYLE}>
          Check that the stack is running, then try the request again.
        </p>
        <button onClick={() => reset()} style={BUTTON_STYLE} type="button">
          Try again
        </button>
      </body>
    </html>
  );
}
