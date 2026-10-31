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
2. Settings → **Google Drive** → paste its local path, e.g. `G:\My Drive\Infamazing\FORGE` → Save.
3. **Sync all now** copies anything made before you set it.

Every save mirrors the video's folder. Retitling a video renames the folder (and the title-named files) locally and in Drive.
Files you add yourself in Drive (edits, thumbnails) are never touched.

## Flow

| Tab | What |
|---|---|
| **Ideas** | **Brainstorm** (direction, count, optional web check of each premise) · **Competitor outliers** (channel links → their recent Shorts at ≥N× channel median, fresh first → pick → Claude niche-bends them into Infamazing topics) · **Add your own** (`title \| angle`). Already-made titles are never re-pitched. Tick ideas → **Write scripts for picked** (+ VO in the same batch). |
| **Videos** | Every video with chars / VO / refs / folder state. Editor: SEO title (= folder + file name), script with live char count against the format's range, **Revise** prompt (optionally with web research), reference links, verified facts, VO panel (Kalemod defaults, overridable). **Mark done** adds the script to past scripts. Tick several → **VO for picked**. |
| **References** | Style library the scripts learn from: past scripts (CSV import, e.g. `infamazing_scripts.csv`, or auto-added on **Mark done**), Shorts links (captions via yt-dlp, ElevenLabs speech-to-text fallback), or pasted text. Tag each with a format. |
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
