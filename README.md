# infamazing-forge

story-forge, but for Infamazing: **ideas → researched script → Kalemod-voice VO → `Videos/<title>/` folder (+ Google Drive).**

Runs on your Windows PC, uses your Claude subscription through the `claude` CLI (no API key), your ElevenLabs key, and the
same voice + VO settings Kalemod uses in story-forge.

## Setup (once)

Clone it next to story-forge (same `GIT Repos` folder), then:

```
powershell -ExecutionPolicy Bypass -File setup.ps1
```

Double-click **`start.bat`** → http://localhost:8778 (story-forge stays on 8777, both can run).

On first start it reads story-forge's `forge.db` and `.env` (read-only) and copies over:
- the last voice you used, its ElevenLabs model / stability / similarity / style
- the dead-air + speed settings (min pause, pause-to, threshold, speed, pitch mode)
- learned voice pace, and `ELEVENLABS_API_KEY` / `YT_API_KEY`

If story-forge lives somewhere else, set `STORY_FORGE_DIR=...` in `.env` and hit **Settings → Re-import from story-forge**.

## Google Drive

The app writes a normal folder and **Google Drive for desktop** uploads it (no Google login inside the app).
Target folder: https://drive.google.com/drive/u/3/folders/1AN5OfgzF5JZO23Gh9Cx9tFhIoq6SaUkN

1. Drive for desktop signed in with the account that owns that folder (it's on your `u/3` account).
   If it was shared with you, open it in Drive → **Add shortcut to Drive** so it shows under My Drive.
2. Leave the path blank and it finds a folder with "infamazing" in its name under My Drive / Shared drives on any
   Drive-for-desktop letter (each signed-in account gets its own). Otherwise paste its local path in Settings →
   **Google Drive** (quotes from Explorer's "Copy as path" are fine) → Save. Saving a path copies every existing video.
3. Every save / script / VO reports `Drive: synced to …`, `no folder set` or `FAILED: …` in the log and Save toast.

Every save mirrors the video's folder. Retitling a video renames the folder (and the title-named files) locally and in Drive.
Files you add yourself in Drive (edits, thumbnails) are never touched.

## Flow

| Tab | What |
|---|---|
| **Ideas** | **Brainstorm** (direction, count, optional web check of each premise) · **Competitor outliers** (channel links → their recent Shorts at ≥N× channel median, fresh first → pick → Claude niche-bends them into Infamazing topics) · **Add your own** (`title \| angle`). Already-made titles are never re-pitched. Tick ideas → **Write scripts for picked** (+ VO in the same batch). |
| **Competitors** | The [competitor scout](COMPETITOR_SCOUT.md) (ported from story-forge): Up next beside your 15 latest Shorts + one hop out from your 3 closest neighbours (requested as `gl=US`, region switchable), top 45 measured (usual/best/Shorts-first/pace/rising/breakouts), active Shorts-first only, brand clones dropped, Claude fit check (5+ kept), closeness 1-10, niche verdict (open / competitive / crowded). Modes: My channel / Like a channel / Niche. Pins always count. **Going off now** = their newest breakouts → pick → ideas (their topic in our structure, or new topic with their hook). Picked channels → outlier finder. Cached 3 days. |
| **Find competitor channels** (Ideas) | Seeds = your **latest scripts** (who already has Shorts on the same stories, and their top Shorts on each), your channel's best recent Shorts, and any videos you paste. For each seed it reads YouTube's **Up next / autoplay** list as a logged-out viewer, maps those videos to channels, and adds Shorts YouTube search returns for the same topics. Paste YouTube Studio → Analytics → Audience → **Channels your audience watches** for the strongest signal. Optional snowball: also checks what plays after your current competitors' best Shorts. Channels are ranked by hits × how many different seeds lead there × Shorts median views, boosted if Shorts-first and active. **Add + find outliers** drops them straight into the outlier finder. |
| **My winning Shorts** (Ideas) | Your channel's latest Shorts that beat your own median (≥1.5×) → pick → **Follow-ups to my winners**: Claude works out why each one won (vein, brand, emotion, hook, format; it reads the script too when the video is in the app) and pitches new true stories in the same vein: other brands, the rival's version, part 2s, flip sides, "which came first" pairings. |
| **Turn outliers into ideas** | **Their topic, our structure** (default): keeps the true story, researches it, re-pitches it in one of our formats with our own title. **New topic, their hook DNA**: the old niche-bend. |
| **Videos** | Every video with chars / VO / refs / folder state. Editor: SEO title (= folder + file name), script with live char count against the format's range, **Revise** prompt (optionally with web research), reference links, verified facts, VO panel (Kalemod defaults, overridable). **Status** (Draft · VO ready · In progress · Live) is a dropdown in the list and the editor; setting **Live** adds the script to past scripts. Filter the list by status. Tick several → **VO for picked**. |
| **References** | Style library the scripts learn from: past scripts (CSV import, e.g. `infamazing_scripts.csv`, or auto-added when a video goes **Live**), Shorts links (captions via yt-dlp, ElevenLabs speech-to-text fallback), or pasted text. Tag each with a format. |
| **Formats** | Script formats are prompt files in `formats/`. Ships with **Classic "Have you ever wondered"** (750-825), **Story reveal + "Like if A, subscribe if B"** (1100-1250) and **Historic Foods escalation** (500-800). Add one later: blank, or **Learn format** from picked references. |

### How a script is made

1. Claude researches the topic with web search, checks every fact, and swaps to the true angle if the premise was wrong.
2. Writes it in the picked format, styled on the picked references (none picked = latest past scripts of that format).
3. Length loop until it lands in the format's char range.
4. Finds YouTube videos on the same story for references; if none exist, non-YouTube sources (and a YouTube API search as a backup when a key is set).
5. Best SEO title → folder name.

## Output

```
Videos/<SEO title>/
  <SEO title>.txt     script text only
  <SEO title>.mp3     clean VO (dead air cut, sped up like Kalemod)
  vo_raw.mp3          straight from ElevenLabs
  references.txt      YouTube refs (or other sources), verified facts, style references used
  video.json
```

## Layout

```
app/server.py     FastAPI routes          app/static/   UI (no build step, story-forge look)
forge/ideas.py    brainstorm + outlier bending        forge/yt.py       YouTube Data API
forge/scripts.py  formats, research + write, length loop, revise
forge/vo.py       ElevenLabs + dead-air, Kalemod settings import
forge/output.py   Videos/ folders + references.txt    forge/drive.py    Drive-for-desktop mirror
forge/ytref.py    references from video links (shared with story-forge)
formats/*.md      script formats ( _global.md = rules for all )
vendor/narration_cleaner/   from thiccoyaki/narration-cleaner
```
