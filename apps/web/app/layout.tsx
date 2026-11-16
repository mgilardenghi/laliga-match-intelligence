/**
 * The document every page is rendered into: the theme, the shell and the slots.
 *
 * The file exists so that every page inherits one header, one navigation, one
 * footer and one theme resolution instead of repeating them, and so that the
 * theme is decided before the first byte of HTML leaves the server.
 */
import type { Metadata } from "next";
import { cookies } from "next/headers";
import type { ReactElement } from "react";

import { Footer, Header, SkipLink } from "@/components/shell";
import shell from "@/components/shell/shell.module.css";
import { parseTheme, THEME_COOKIE } from "@/lib/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: "LaLiga Match Intelligence",
  description:
    "Predicts La Liga matches, simulates the rest of the season and measures its own accuracy.",
};

/**
 * The root layout.
 *
 * The theme cookie is read here, on the server, so the `data-theme` attribute
 * is already in the first paint: a client-side correction afterwards would show
 * the wrong theme for a frame. Reading a cookie also makes every page dynamic,
 * which is what the dashboard wants, since all of it is read per request.
 */
export default async function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>): Promise<ReactElement> {
  const cookieStore = await cookies();
  const theme = parseTheme(cookieStore.get(THEME_COOKIE)?.value);

  return (
    <html lang="en" data-theme={theme ?? undefined}>
      <body>
        <SkipLink />
        <Header />
        <main className={shell.main} id="main">
          {children}
        </main>
        <Footer />
      </body>
    </html>
  );
}
