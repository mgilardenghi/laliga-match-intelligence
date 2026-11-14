# API-Football FREE plan: player-level data for Valencia CF

Research date: 2026-11-12 · Branch: `fleet/verify-api-football` · Scope: can Phase 2
(player analysis) be planned on API-Football's free plan for the current 2026/27
La Liga season?

Every claim below is tagged:

- `[docs]` — read in the vendor's documentation, pricing page or terms. Not
  observed on a live response.
- `[live]` — observed on a live API response in this session.
- `[derived]` — arithmetic or inference from this project's needs, not a vendor claim.

**No `[live]` claim exists in this document.** See "Verification status".

## Verification status

This page records two things: the vendor's documented behaviour (plan limits, the
free season window, terms of use and endpoint shapes, each quoted with its URL and
marked `[docs]`), and the live probe written to check it.

The research session itself made no API requests, because agents are not allowed to
handle the API key. The probe in the appendix was then run by hand on 2026-11-12.
Its outcome is summarized in [`docs/roadmap.md`](../roadmap.md) (Phase 2): the free
plan covers seasons 2022 to 2024 plus the current squad, the current season needs a
paid plan, and the terms grant no publication license. That outcome agrees with
the verdict below.

The per-endpoint live values were not transcribed into this page, so the Status
column of the endpoint table still reads `not observed`. Treat the tables as the
documented shape, not as recorded responses.

## Verdict

**NOT USABLE for the current season.** For Valencia CF player analysis in the
current 2026/27 season the FREE plan is not usable: the free season window ends at
`Year-2`, which is 2024 in 2026, so `season=2026` (and `season=2025`) fall outside
it `[docs]`. Basis is documentation, not a live refusal — see above.

**USABLE WITH LIMITS for historical seasons.** The same free plan documents player
and match-detail data for 2022, 2023 and 2024 `[docs]`, which supports a Phase 2
prototype on finished seasons only, not the live 2026/27 product view.

## Free plan limits `[docs]`

From <https://www.api-football.com/pricing> and
<https://www.api-football.com/news/post/how-ratelimit-works> (2026-06-12):

- "Free — $0 Month — 100 Requests / day".
- "Free plan: 10 requests per minute." Per-minute and daily limits apply together,
  and are also applied per source IP, not only per key.
- Exceeding quota: "once your quota is reached, the API automatically stops
  processing requests and sends an error message instead of billing extra".
- Every response carries `x-ratelimit-requests-limit`,
  `x-ratelimit-requests-remaining`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`.
- Rejection shape documented for the per-minute limit: HTTP 429 with
  `"errors": {"rateLimit": "Too many requests. You have exceeded the limit of
  requests per minute of your subscription."}` and `"results": 0`.
- Documented warning: sustained over-limit bursts "can be temporarily or
  permanently blocked by the firewall without prior warning".
- `/status` "does not count against the daily quota" `[docs]`, but it is still a
  GET and counts against the per-minute cap `[derived]`.
- Documentation inconsistency worth knowing: the news post (2026-03-13) quotes Pro
  at $19, Ultra $29, Mega $39, while the pricing page shows Pro $16, Ultra $26,
  Mega $36. Treat the pricing page as current and the daily quotas as the stable
  numbers.

## Free season window `[docs]`

Direct quote, <https://www.api-football.com/news/post/how-to-get-all-data-from-a-league-with-api-football>
(2026-10-26):

> This exact season is not available on the Free Plan in 2026. Free access covers
> seasons from `Year-4` through `Year-2`, which currently means 2022, 2023 and
> 2024. If you use the Free Plan, replace `2021` with an available season […] You
> can check the seasons available with your plan from your dashboard.

Applied to this project (season parameter = start year):

| `season` | Season | Free plan window | Expected answer |
| --- | --- | --- | --- |
| 2026 | 2026/27 (current) | outside (`Year-0`) | refused `[docs]` |
| 2025 | 2025/26 | outside (`Year-1`) | refused `[docs]` |
| 2024 | 2024/25 | inside (`Year-2`) | served `[docs]` |
| 2023 | 2023/24 | inside (`Year-3`) | served `[docs]` |
| 2022 | 2022/23 | inside (`Year-4`) | served `[docs]` |

A refused season is documented to return a normal envelope with `errors` populated
and an empty `response`; the exact message text is not quoted here because it was
neither read in the fetched pages nor observed live.

Consequence for the product: the MVP's live-tracking rule ("predictions stored
before kickoff, scored after the result arrives") and the team-focus view need the
running season. The free window is a moving one-season-lagged archive, so it can
never serve the current season. The window slides: in 2027 the free range becomes
2023–2025.

## Endpoint availability for Valencia

IDs are not verified live in this session. League `140` for La Liga and team `532`
for Valencia are the values this probe is written against; the probe derives the
team id from `/teams?search=Valencia` instead of trusting the constant.

Status column: all rows are `not observed`: the live values were not transcribed here (see Verification status). "Documented
answer" is what the vendor reference says the endpoint returns.

| Endpoint | Season | Status | Results / paging | Main fields (names only) |
| --- | --- | --- | --- | --- |
| `/status` | — | not observed | 1 object | `account`, `subscription.plan`, `subscription.active`, `subscription.end`, `requests.current`, `requests.limit_day` |
| `/leagues?id=140` | — | not observed | 1 league, `seasons[]` per year | `league`, `country`, `seasons[].year`, `seasons[].current`, `seasons[].coverage.fixtures.{events,lineups,statistics_fixtures,statistics_players}`, `coverage.{standings,players,top_scorers,top_assists,top_cards,injuries,predictions,odds}` |
| `/teams?league=140&season=2026` | 2026 | not observed | 20 teams | `team.{id,name,code,country,logo}`, `venue` |
| `/teams?search=Valencia` | — | not observed | 1–2 teams | same as above |
| `/players?team=<id>&season=<s>` | 2026/25/24 | not observed | 20 players per page, `paging.total` | `player.{id,name,age,nationality,height,weight,photo,injured}`, `statistics[].team`, `statistics[].league`, `statistics[].games`, `statistics[].shots`, `statistics[].goals`, `statistics[].passes`, `statistics[].tackles`, `statistics[].duels`, `statistics[].dribbles`, `statistics[].fouls`, `statistics[].cards`, `statistics[].penalty` |
| `/players/squads?team=<id>` | — (current squad) | not observed | 1 team block | `team`, `players[].{id,name,age,number,position,photo}` |
| `/fixtures?team=<id>&season=<s>` | 2026/25/24 | not observed | 1 row per match | `fixture.{id,date,timestamp,venue,status}`, `league`, `teams`, `goals`, `score` |
| `/fixtures/players?fixture=<id>` | any played match | not observed | 2 teams × squad | `team`, `players[].player`, `players[].statistics[].games`, `.offsides`, `.shots`, `.goals`, `.passes`, `.tackles`, `.duels`, `.dribbles`, `.fouls`, `.cards`, `.penalty` |
| `/fixtures/events?fixture=<id>` | any played match | not observed | 1 row per event | `time.{elapsed,extra}`, `team`, `player`, `assist`, `type`, `detail`, `comments` |
| `/fixtures/lineups?fixture=<id>` | any played match | not observed | 2 teams | `team`, `formation`, `coach`, `startXI[]`, `substitutes[]` |
| `/fixtures/statistics?fixture=<id>` | any played match | not observed | 2 teams × ~15 rows | `team`, `statistics[].type`, `statistics[].value` |
| `/injuries?team=<id>&season=<s>` | 2026/25/24 | not observed | 1 row per injury | `player`, `team`, `fixture`, `league`, `type`, `reason` |
| `/standings?league=140&season=<s>` | 2026/25/24 | not observed | 1 league, 20 rows | `rank`, `team`, `points`, `goalsDiff`, `form`, `all/ home/ away.{played,win,draw,lose,goals.{for,against}}`, `status`, `description`, `update` |

Coverage flags are the contract to read first: `seasons[].coverage` tells which data
types exist for a league-season, and the free plan can still return `true` there for
a season it refuses to serve `[derived]` — the flag is about vendor coverage, the
refusal is about the plan. Confirm both in the live run.

Row counts in the table ("20 teams", "20 players per page", "1 row per event") are
expected counts, not observed `results` values; only `paging` semantics for
`/players` (20 per page) is documented.

Field-level detail for `/players` and `/fixtures/players` was cross-checked against
a third-party endpoint summary, because the official documentation site answered
this host with a bot challenge (HTTP 403 / Cloudflare) on 2026-11-12. The field
lists are documented names, not observed payloads.

## Player statistics fields

Documented structure of one `/players` row `[docs]` (types; no values, because no
live payload was obtained):

```text
player:        { id:int, name:str, firstname:str, lastname:str, age:int,
                 birth:{date,place,country}, nationality:str, height:str,
                 weight:str, injured:bool, photo:str }
statistics[]:  { team:{id,name,logo}, league:{id,name,country,logo,flag,season},
                 games:{ appearences:int, lineup:int, minutes:int,
                         number:int|null, position:str, rating:str|null,
                         captain:bool },
                 substitutes:{ in:int, out:int, bench:int },
                 shots:{ total:int|null, on:int|null },
                 goals:{ total:int|null, conceded:int, assists:int|null,
                         saves:int|null },
                 passes:{ total:int|null, key:int|null, accuracy:int|null },
                 tackles:{ total:int|null, blocks:int|null,
                           interceptions:int|null },
                 duels:{ total:int|null, won:int|null },
                 dribbles:{ attempts:int|null, success:int|null,
                            past:int|null },
                 fouls:{ drawn:int|null, committed:int|null },
                 cards:{ yellow:int, yellowred:int, red:int },
                 penalty:{ won:int|null, commited:int|null, scored:int|null,
                           missed:int|null, saved:int|null } }
```

Notes for Phase 2 design:

- `rating` is a string (a decimal formatted as text), not a number; it is `null`
  for matches without a rating.
- Statistics are grouped per competition. A player with La Liga and Copa del Rey
  appearances returns two `statistics[]` blocks; totals must be summed by the
  consumer `[docs]`.
- The vendor's own spelling is `appearences` and `commited` (sic). Do not
  "correct" these keys in code.
- `goals.conceded` and `goals.saves` matter only for goalkeepers.
- No expected-goals field exists in v3 match statistics `[docs]`, so Phase 2 cannot
  take xG from this source.

## Quota used

| Measurement | Value |
| --- | --- |
| Budget from the brief | 40 requests |
| Requests used by the research session | 0 (the probe was run by hand later) |
| `/status` before / after | not executed |
| Requests planned for the probe | 22 (dry run output: 18 scheduled, plus 4 match-detail calls when a finished fixture exists) |

## Rate limit behaviour `[docs]`

- 10 requests per minute on the free plan, plus 100 per day. Both are enforced, and
  limiting is applied per key and per source IP, so a shared host can burn the quota
  faster than one process expects.
- Over the per-minute limit: HTTP 429, `errors.rateLimit`, `results: 0`. The vendor
  asks clients to back off progressively; repeated bursts can be blocked by the
  firewall without notice.
- Practical pacing: one request every 6–7 seconds stays inside 10/min. The probe
  script defaults to `SLEEP_BETWEEN=7` for that reason.

## Terms of use `[docs, read in docs]`

Read in <https://www.api-football.com/terms> on 2026-11-12 (retrieved through a
search cache because the site serves a bot challenge to direct fetches). Quoted
verbatim:

> Accounts are individual and may not be shared with other developers. It is
> forbidden to have multiple accounts to increase the limit of the free plan. The
> data we provide is obtained from partners, it is prohibited to resell this data
> to third parties or to use the services provided for illegal purposes. The resale
> of our data without permission is not permitted.

> We do not provide a "license" for the use and publication of the data provided by
> our services on applications, websites or any other products made by the user. Any
> license or permission to publish the data must be requested by the user from the
> competent authorities.

> A free plan is automatically activated when you register on the dashboard. This
> gives you access to all available APIs and endpoints, but remains limited in terms
> of available data. We reserve the right to modify the Free Plan and the available
> data at any time without prior notice.

> The data is provided "as is" and without any guarantee. […] Incorrect or missing
> data does not constitute a valid reason for a full or partial refund.

> Logos, images and trademarks delivered through the API are provided solely for
> identification and descriptive purposes […] Some images or data may be subject to
> intellectual property or trademark rights held by third parties […] The use of such
> content in your applications, websites, or products may require additional
> authorization or licensing from the respective rights holders.

What this means for this repository:

1. Building a local, non-commercial portfolio app that displays this data is the
   vendor's stated intended use ("We provide data for you to create different
   projects such as applications, websites, fantasy soccer games etc."), but the
   vendor grants no publication licence and passes the rights question to the user.
   A public, resale-like or commercial use needs permission in writing.
2. The vendor can change or withdraw free-plan data at any time without notice, so
   the free plan cannot be a load-bearing source for a public demo.
3. Storing data locally is not prohibited by the quoted text, but no retention or
   caching allowance is granted either. **Null result:** the terms as read contain no
   explicit retention window, cache TTL or storage limit. Treat long-term storage of
   API-Football payloads as unresolved and ask the vendor before relying on it.
4. Logos, club crests and player photos are third-party trademarks. This conflicts
   with `AGENTS.md` ("Never add club crests, logos or other trademarked images"), so
   a Phase 2 UI must not use `team.logo`, `league.logo`, `flag` or `player.photo`
   from this source.
5. One account per developer, no extra accounts to widen the free quota.

## Request budget to keep one team updated per matchday `[derived]`

Assumes the plan served the current season and a Valencia matchday needs its own
players updated after the match.

| Call | Requests | Frequency |
| --- | --- | --- |
| `/fixtures?team=<id>&season=2026` (find the played match) | 1 | per matchday |
| `/fixtures/players?fixture=<id>` | 1 | per played match |
| `/fixtures/events?fixture=<id>` | 1 | per played match |
| `/fixtures/lineups?fixture=<id>` | 1 | per played match |
| `/fixtures/statistics?fixture=<id>` | 1 | per played match |
| `/players?team=<id>&season=2026` (20 per page, squad of 24–28 → 2 pages) | 2 | per matchday |
| `/injuries?team=<id>&season=2026` | 1 | per matchday |
| `/standings?league=140&season=2026` | 1 | per matchday |
| `/players/squads?team=<id>` (squad changes) | 1 | weekly |
| `/leagues?id=140`, `/teams` (reference data) | 2 | once, then cached |

Total: about **9 requests per matchday**, about **10 with the weekly squad refresh**.
Two matchdays in a week is 18–20 requests. The 100/day quota is comfortable; the
10/minute cap is the binding constraint during a backfill, where 200 matches of
detail data at 6–7 s per request is roughly 25 minutes of wall clock per 200
requests, and a full-season backfill of four detail calls per match is multiple
days at 100 requests per day.

## Bottom line for the roadmap

- Do not plan the live 2026/27 player view on the free plan: the season is outside
  the documented free window.
- Two viable paths, both outside this task's scope:
  1. Phase 2 prototype on finished seasons 2022/23–2024/25 with the free plan, no
     cost, no publication rights, and no current-season claim.
  2. A paid plan for the current season. Pricing page: Pro $16/month, 7,500
     requests/day, 300 requests/minute `[docs]`.
- Keep player data out of the public demo until the publication-rights question is
  answered, and never use vendor logos or photos (see `AGENTS.md`).

## Appendix: minimal live probe

This condensed probe settles the verdict question:

```bash
# run by a human with API_FOOTBALL_KEY exported in the shell; ~7 s apart to stay under 10 requests/minute
h=(-H "x-apisports-key: $API_FOOTBALL_KEY")
u=https://v3.football.api-sports.io

curl -s "${h[@]}" "$u/status" | jq '.response.subscription, .response.requests'
curl -s "${h[@]}" "$u/leagues?id=140" | jq '.response[0].seasons[] | {year, current, coverage}'
curl -s "${h[@]}" "$u/teams?league=140&season=2026" | jq '.errors, .results'
curl -s "${h[@]}" "$u/teams?search=Valencia" | jq '.response[].team | {id, name}'

for s in 2026 2025 2024; do
  sleep 7
  curl -s "${h[@]}" "$u/players?team=532&season=$s" \
    | jq -c --argjson s "$s" '{season: $s, errors, results, paging}'
done

# one played fixture from the newest season that answered, then its detail blocks
curl -s "${h[@]}" "$u/fixtures?team=532&season=2024" | jq -r '[.response[] | select(.fixture.status.short=="FT")][-1].fixture.id'
# for <fid>: /fixtures/players, /fixtures/events, /fixtures/lineups, /fixtures/statistics

curl -s "${h[@]}" "$u/status" | jq '.response.requests'
```

Interpretation: if the `players` calls for 2026 and 2025 return a populated
`errors` object with `results: 0`, the documented verdict holds. If they return
players, the verdict line at the top of this document must be changed to USABLE
WITH LIMITS and the quota line updated.

No raw API response is committed to this repository.
