/**
 * The stable entry point of the focus mode.
 *
 * The navigation points at `/focus` without naming a team, so the target has to
 * be resolved on every request: the cookie wins, then the configured club. A
 * resolved team redirects to its own page; without one the reader is sent to
 * the teams index, which is where a focus team is chosen, with the notice that
 * asks them to pick one. This route renders nothing of its own.
 */
import { redirect } from "next/navigation";

import { resolveFocusTeam } from "@/lib/focus";

export const dynamic = "force-dynamic";

export default async function FocusRedirectPage(): Promise<never> {
  const team = await resolveFocusTeam();

  // Both calls stay outside a `try`: `redirect` signals by throwing, and a
  // catch would swallow the navigation instead of forwarding it.
  if (team !== null) {
    redirect(`/focus/${team.id}`);
  }
  redirect("/teams?pick=focus");
}
