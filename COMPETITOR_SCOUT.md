# Competitor scout: finding the channels that really compete with you

A portable method (used by story-forge, meant for infamazing-forge too). It's the approach Legion uses, written
down from studying its files, plus a few fixes for what went wrong in practice.

**Core idea:** don't search for competitors by keyword. Ask YouTube. The channels YouTube puts in the **Up next**
sidebar next to your videos are the ones competing for the same viewers, even when they use different words. Then
measure each one and filter hard.

---

## 1. Audience overlap scan

1. Take your channel's **15 most recent Shorts** (or, with no channel yet, the biggest Shorts of a niche search).
2. For each, open its watch page and read the **Up next** list (`ytInitialData` → `secondaryResults`). Collect
   the channel of every recommended video.
3. Count how many of the 15 videos each channel appears next to, then grade it:

   | Grade | Appears next to |
   |---|---|
   | **close** | half or more of your videos |
   | **medium** | a fifth or more |
   | **far** | fewer |

4. **One hop out:** do the same for 5 Shorts of each of your **3 closest** channels. Small niche channels rarely
   appear next to yours, but they do appear next to a neighbour's. Mark these `hop 2`.
5. Cache a week. Space the requests out (1 to 2 seconds apart).

**Fix for local noise.** The sidebar is personalised by country. Read from the Philippines, Kalemod's list included
ABS-CBN, GMA and Pinoy Big Brother, and one hop out pulled in MrBeast. So:

- request the page with `hl=en&gl=US` (or your audience's country) and an `Accept-Language` header,
- and let the fit check (step 3) drop channels whose language or region doesn't match yours.

## 2. Measure each channel (YouTube Data API)

Only the top ~45 channels by appearances get measured (quota). For each:

| Number | How |
|---|---|
| **usual** | median views of its last ~30 Shorts, leaving out Shorts under 3 days old (still growing). Median, never the average: one viral Short would wreck an average |
| **best** | highest-viewed of those |
| **Shorts-first** | share of its uploads that are Shorts (uploads playlist `UU…` vs Shorts playlist `UUSH…` totals) |
| **pace** | Shorts per week over the last 30 days; days since its last Short |
| **rising** | median of its newer half of Shorts vs its older half |
| **breakouts** | Shorts at 3x or more of its usual, adjusted for age (a Short a day old is compared with what its Shorts usually have at that age, approximated as usual x age factor) |
| **subs, started** | channel statistics and creation date (for "small channel" and "new channel") |

Keep only **active, Shorts-first** channels: at least half Shorts, and a Short in the last 30 days.

**Drop brand clones:** a channel whose name contains yours ("Kalemod Clips", "MrBeast 2") is never a competitor.

## 3. Fit check (one AI call for the batch)

Give the AI your channel (name, description, recent titles) and each candidate (name, recent titles, top hits).
It returns **fit 0 to 10 with a one-line reason** per channel ("Roblox animated premise Shorts, similar hooks",
"Roblox comedy skits, not narrated stories"). Score 0 when the language or region doesn't fit. Keep **fit 5+**.

Without an AI, fall back to shared topic words between titles.

(Legion also compares thumbnails to yours. Optional; needs a vision model.)

## 4. Closeness score (1 to 10, for the map)

A blend of: overlap grade (close / medium / far / hop 2), fit, shared topic words in recent titles, and how near
its usual views are to yours (same size league).

## 5. Niche verdict

From the kept channels only, never a keyword search:

- what a Short usually gets there (median of their usuals),
- **do small channels win?** (under 100k subs with a breakout),
- **do new channels win?** (started in the last 6 months, with a breakout),
- **what's hot now** (breakouts in the last 7 days).

Verdict: **open** (small or new channels are breaking out), **competitive** (winners exist but they're
established channels) or **crowded** (few breakouts, big usuals).

## 6. Pinned channels always count

Channels you add by hand stay in, whatever the fit check says, and are measured like the rest. They feed
everything the scan feeds.

## 7. What the scan feeds

- **Going off now:** the newest Shorts of every kept and pinned channel, each measured against its own channel's
  usual (age-adjusted), best first. Near matches add a few breakouts, labelled "outside your niche".
- **Ideas / concepts:** the competitors' latest winners as the source material.
- **Styles:** build a style from a competitor in one click.

## Modes

| Mode | Starts from |
|---|---|
| **My channel** | your own Shorts (default) |
| **Like a channel** | any channel you paste |
| **Niche** | Shorts found by searching a term (when there's no channel to start from) |

Cache the whole scan 3 days per mode and input.

---

## Implementation notes (story-forge: `forge/scout.py`, Competitors tab)

- Up next is read with plain HTTP from the user's own PC (where the app runs), not from a cloud server.
- The watch page holds `var ytInitialData = {...};`. The sidebar items are either `compactVideoRenderer` (older
  layout) or `lockupViewModel` (newer). Parse generically: per item, the first `UC…` `browseId` is the channel
  and the first `videoId` is the video, so a layout change doesn't break it.
- YouTube Data API quota for one full scan: roughly 150 to 300 units of the 10,000 daily.
