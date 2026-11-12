import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "LaLiga Match Intelligence",
  description:
    "Predicts La Liga matches, simulates the rest of the season and measures its own accuracy.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
