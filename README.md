# winnower

Finds licensed audio online, keeps the aurally interesting phrases, and stores each one
as a FLAC file with a JSON sidecar describing it. Regions are found by timbral change, and
anything too uniform (hum, room tone, plain noise) is rejected. Every phrase is then cut to
a whole number of bars and baked into a seamless loop: on the recording's own beat when it
has one, and on a beat fitted to the sound when it doesn't (rain, drones, crackle).
Samples are filed by key and named by key, tempo, length and a description. Designed to run
on a Raspberry Pi 5 or a small server.

Background and design decisions: the research report "Sample crawler stack" (Claude Doc).

## Status (v0.2)

- [x] Freesound search adapter (API key; HQ OGG previews), rate limited to 60/min, 2,000/day
- [x] Keyless sources: Openverse, Internet Archive, Wikimedia Commons, NASA, ccMixter,
      CSV/JSONL manifests (datasets) and plain web pages (see [Sources](#sources))
- [x] Licence gate: CC0/PD and CC BY (any version) allowed, NC/SA/Sampling+/NASA kept but
      flagged, ND and unlicensed audio skipped (unless `--include-unlicensed`). Attribution
      text stored with every sample.
- [x] Timbral segmentation (ruptures PELT, L2 cost, linear memory)
- [x] Region scoring: quality, stationarity, loop point, rhythmicity, trend, and an
      `interest` score with a floor
- [x] Phrases of 2 to 24 s, aiming at 10 s; diverse selection per file. No one-shots.
- [x] Beat grids: tempo and downbeat detected for steadily pulsed recordings, fitted for
      arrhythmic ones; every cut is 1, 2, 4 or 8 bars at a whole-number BPM (70 to 150)
- [x] Seamless loops: the crossfade is baked in, so the file loops in any player or DAW
- [x] Key (Krumhansl-Schmuckler on chroma), Camelot folders, descriptive file names,
      BPM/key/attribution written into the FLAC tags
- [x] Local files and folders (`harvest local`): album tags read with mutagen, files already
      processed are skipped, long files analysed in 10-minute chunks. Without `--licence`,
      samples are flagged `local-unknown` and left out of `list --commercial`.
- [x] Local storage now, S3 later by changing one setting
- [x] Derived index (`index/samples.jsonl`, plus LanceDB if installed)
- [ ] Lossless originals via Freesound OAuth2 (v0 cuts from the preview, marked
      `analysed_from: "preview"`)
- [ ] CLAP embeddings, AudioSet tags, text search
- [ ] Ratings and taste profiles that steer harvests
- [ ] Keyed sources: Europeana, Jamendo, Smithsonian Open Access, Brave Search

## Install

```sh
uv sync                          # add --extra index for LanceDB, --extra s3 for buckets
. .venv/bin/activate             # or prefix commands with `uv run`
cp winnower.example.toml winnower.toml
echo 'FREESOUND_TOKEN=...' >> .env   # https://freesound.org/apiv2/apply
```

On a Raspberry Pi, use 64-bit Raspberry Pi OS; every Python dependency has Linux ARM wheels.
Install `ffmpeg` (`sudo apt install ffmpeg`) to read AAC/M4A, ALAC and other formats
libsndfile can't.

## Use

```sh
winnower harvest freesound "bowed metal" --limit 20
winnower harvest openverse "rain" --license-type commercial
winnower harvest archive "field recording" --collection radio-aporee-maps
winnower harvest wikimedia "thunder"
winnower harvest nasa "plasma"
winnower harvest ccmixter --tags ambient,field_recording
winnower harvest manifest fsd50k.csv         # url|path, licence, title, author, tags, ...
winnower harvest web https://some-netlabel.example/release/ --depth 1
winnower harvest local ~/Music/albums        # folders are scanned recursively
winnower harvest local ~/netlabel-ep --licence https://creativecommons.org/licenses/by/4.0/
winnower list --min-interest 0.6 --type loop
winnower list --camelot 8A --compatible --bpm 90-110   # mixes with A minor, 90-110 BPM
winnower list --key F#m --min-length 8 --min-seam 0.6
winnower list --commercial                   # only licences allowed for release
winnower reindex                             # rebuild index from sidecars
```

Every harvest takes `--limit` (source files to process) and `--max-duration` (seconds).
Re-running a harvest skips files already processed. If a search fails or a daily quota
runs out, the run stops with a message and keeps what it already harvested.

### Keys

Keys can come from, highest priority first:

1. a flag: `winnower harvest freesound "rain" --token ...` (also `harvest openverse --token`)
2. an environment variable: `FREESOUND_TOKEN`, `OPENVERSE_TOKEN`
3. a `.env` file of `KEY=value` lines: `./.env` is read if present, or name one with
   `winnower --env-file ~/keys.env ...`. It's git-ignored. Variables already set in the
   environment win over the file.
4. `winnower.toml` (`freesound_token`, `openverse_token`)

Any `WINNOWER_*` variable (`WINNOWER_USER_AGENT`, `WINNOWER_STORAGE_ROOT`,
`WINNOWER_MIN_INTEREST`) can go in `.env` too.

## Sources

| Source | Key needed? | Env var / setting | What it holds | Licence quality | Default rate |
| --- | --- | --- | --- | --- | --- |
| Freesound (`freesound`) | Yes, free | `FREESOUND_TOKEN` / `freesound_token` | Sound effects, field recordings, textures | Per-file CC, reliable | 60/min, 2,000/day |
| Openverse (`openverse`) | No (optional token) | `OPENVERSE_TOKEN` / `openverse_token` | Index of Jamendo, Wikimedia, ccMixter and more | Per-file CC, reliable | Anonymous 5/hour burst, 100/day, 20 per page; with a token 100/min, 10,000/day |
| Internet Archive (`archive`) | No | | Netlabels, radio aporee field recordings, old radio, live music | `licenseurl` per item, often missing (filtered out unless `--include-unlicensed`) | 30/min |
| Wikimedia Commons (`wikimedia`) | No | `WINNOWER_USER_AGENT` (see below) | Field recordings, instruments, speech, historical audio | Per-file, reliable | 60/min |
| NASA (`nasa`) | No | | Launches, mission audio, space sounds, podcasts | US government work (`NASA-Media`, flagged) | 60/min |
| ccMixter (`ccmixter`) | No | | Remixes, stems, a cappellas | Per-upload CC, reliable | 30/min, 5 uploads per page |
| Manifest (`manifest`) | No | | Whatever you list: FSD50K, Clotho, the FMA dump, your own catalogue | Whatever the manifest says | 60/min for URLs |
| Web pages (`web`) | No | | Netlabel release pages, artists' sites | Detected from the page (`rel="license"`, meta/JSON-LD, CC links), often absent | 1/s per host; respects robots.txt |

Openverse skips Freesound results unless `--include-freesound`; `harvest freesound`
covers them with better previews. Openverse is behind a Cloudflare challenge on some
networks; if you get a 403, it isn't the query.

ccMixter: search works, but in November 2026 its file downloads returned 403 to
non-browser clients, so `harvest ccmixter` currently finds items and then reports them as
errors. The adapter is kept for when that changes.

Not built yet, need a key:

- Europeana (`wskey`, free)
- Jamendo (`client_id`, free)
- Smithsonian Open Access (api.data.gov key)
- Brave Search API (paid tiers)

Deliberately not supported:

- Commercial sample libraries: their terms forbid scraping.
- YouTube and SoundCloud: downloading breaks their terms of service.
- BBC Sound Effects and Pixabay: custom licences that don't fit the licence gate.

### File choice

Each sidecar records `analysed_from`: `original` when the uploaded file (or a lossless
copy of it) was analysed, `preview` for a lossy transcode. The Internet Archive adapter
prefers a lossless original, then a lossless derivative, a lossy original and finally
IA's Ogg or MP3 transcode. Downloads over 200 MB are refused, so a Raspberry Pi never
buffers a 2 GB WAV.

### User-Agent

Requests send `winnower/0.1 (+sample research tool)`. Wikimedia asks for contact details
in the User-Agent; set your own:

```sh
export WINNOWER_USER_AGENT="winnower/0.1 (https://your.site; you@example.org)"
```

## Licences

`[licences]` in `winnower.toml` sorts each licence into `allow` (use freely), `flag`
(keep but mark) or deny (skipped at harvest time; anything not listed). An entry
without a version matches every version (`CC-BY` covers `CC-BY-2.5` and `CC-BY-4.0`);
a versioned entry (`CC-BY-4.0`) matches only that version. Defaults:

```toml
allow = ["CC0-1.0", "PDM", "CC-BY"]
flag = ["CC-BY-NC", "CC-BY-SA", "CC-BY-NC-SA", "Sampling+", "NASA-Media"]
```

No-derivatives (ND) licences are always denied: a sample is a derivative.
`winnower list --commercial` shows only allowed samples.

### Unlicensed audio

Audio with no stated licence is all rights reserved by default. `--include-unlicensed`
(on `archive`, `manifest` and `web`) keeps it anyway, as `web-unknown`: flagged, without
a licence in its attribution, and left out of `list --commercial`. Use it to study or
sketch with, and clear the rights before releasing anything. Recognised denials, such as
ND, are never overridden. The web adapter respects robots.txt. None of this is legal
advice.

## Storage layout

`storage_root` is a local path now (`./data` or `file:///data/samples`) and can become
`s3://bucket/samples` later with no code changes: everything goes through `Store`.

```
sources/{provider}/{provider_id}/meta.json   API response, licence, status
samples/{camelot} {key}/{name}.flac          the cut phrase, e.g. samples/8A A minor/
samples/{camelot} {key}/{name}.json          sidecar: the source of truth
samples/no key/{name}.flac|json              noise, crackle, most percussion
index/samples.jsonl                          derived, rebuildable
```

Names read `{key}_{bpm}bpm_{bars}bar_{length}s_{description}_{id6}`, for example
`Am_96bpm_4bar_10.0s_pulsing-dark-samba-de-fato-1932_29424a.flac` or
`nokey_108bpm_4bar_8.9s_crackling-airy-rain-on-tin-roof_8f31c0.flac`. The description is
up to two words from the analysis (pulsing, crackling, drifting, tonal, dark, bright,
airy; rising or falling for gestures), `gesture` for a gesture, then the start of the
source title. Folders follow the Camelot wheel: neighbouring numbers, and A/B of the same
number, mix harmonically (`list --camelot 8A --compatible`).

Samples harvested before v0.2 stay where they were, in `samples/{id[:2]}/{id}.flac`.

## Phrases, beats and loops

Each candidate phrase gets a beat grid:

- **Detected**: the whole recording's tempo is estimated, then re-estimated in 8 s windows.
  If most windows agree (allowing for double and half time) and the beats stand out from
  the onsets around them, the recording has a steady pulse. Each phrase then uses that tempo,
  refined locally to follow a drifting live band, starts on the downbeat with the most onset
  energy, and is moved onto the attack sample-accurately so the first hit isn't clipped.
- **Fitted**: rain, drones and crackle give a different tempo in every window, so the sound
  chooses instead. Every length that is 1, 2, 4 or 8 bars at a whole-number BPM from 70 to
  150 is tried, from starts in the first 3 s of the candidate, and the one whose end flows
  best back into its start (spectra around both points compared) wins, with a soft
  preference for lengths near 10 s.

Phrases are `loop`s, unless they move one way (`gesture`: a swell, a rising sweep), in which
case they are cut to bars for length alone and not looped. A loop file is exactly
`beats × 60 / bpm` seconds long. The audio just after the loop end is crossfaded into its
first 30 to 80 ms (linear when the two sides are alike, equal-power when they aren't), so it
wraps without a click and needs no loop points. `region.seam` (0 to 1) says how invisible
the wrap is in timbre; filter with `list --min-seam`.

Sidecar fields: `region.{bpm, beats, bars, tempo_source, tempo_confidence, seam,
crossfade_ms}` and `key.{tonic, mode, name, short, camelot, confidence}` (`null` when
there's no key). Lengths and tempo range are set under `[analysis]` in `winnower.toml`
(`min_duration_s`, `target_duration_s`, `max_duration_s`, `bpm_min`, `bpm_max`).

## How regions are scored

All scores are 0 to 1 and stored in the sidecar.

| Score | Meaning |
| --- | --- |
| `interest` | Weighted mix of motion, modulation (0.5 to 20 Hz), spectral richness, micro-event rate, frame diversity and stereo movement. Regions under `min_interest` (default 0.35) are dropped. |
| `stationarity` | Stability of ~2 s block statistics. High for rain, low for a sweep. |
| `loopability` | The beat grid's seam: spectral match around the loop start and end. |
| `rhythmicity` | Periodic pulses at 30 to 240 BPM. Neutral in the ranking. |
| `trend` | One-way movement in loudness or brightness (gestures). |
| `quality` | Silence, clipping, encoder low-pass cliffs. |

The interest weights are hand-set and checked against synthetic sounds in
`tests/synth.py` (sine, hum and white noise must fail; rain, crackle and sweeps must pass).
They are a starting point to be replaced by models learned from your ratings. The rank is
`interest × quality`, nudged up for lengths near the 10 s target and, for loops, for a
good seam.

Known gap: slow continuous gestures can get split into several short segments; merging
adjacent segments with a consistent trend is a planned improvement.

## Development

To add a source, write `src/winnower/sources/<name>.py` with an adapter (a `search()`
that yields `SourceItem`s and a `fetch_audio()`), end it with a `SOURCE` declaring the
subcommand's name, arguments and how to open it (see `sources/spec.py`), and add it to
`SOURCES` in `sources/registry.py`. Deleting a line there removes a source from the CLI.

```sh
pytest -q
ruff check src tests && ruff format src tests
```
