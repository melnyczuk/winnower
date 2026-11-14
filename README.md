# winnower

Finds licensed audio online, keeps the aurally interesting, textural parts, and stores
each one as a FLAC file with a JSON sidecar describing it. Built for arrhythmic, textural
material: regions are found by timbral change rather than beats, and anything too uniform
(hum, room tone, plain noise) is rejected. Designed to run on a Raspberry Pi 5 or a small
server.

Background and design decisions: the research report "Sample crawler stack" (Claude Doc).

## Status (v0)

- [x] Freesound search adapter (API key; HQ OGG previews), rate limited to 60/min, 2,000/day
- [x] Keyless sources: Openverse, Internet Archive, Wikimedia Commons, NASA, ccMixter,
      CSV/JSONL manifests (datasets) and plain web pages (see [Sources](#sources))
- [x] Licence gate: CC0/PD and CC BY (any version) allowed, NC/SA/Sampling+/NASA kept but
      flagged, ND and unlicensed audio skipped (unless `--include-unlicensed`). Attribution
      text stored with every sample.
- [x] Timbral segmentation (ruptures PELT, L2 cost, linear memory)
- [x] Region scoring: quality, stationarity, loop point, rhythmicity, trend, and an
      `interest` score with a floor
- [x] Region types: `texture_loop`, `texture`, `gesture`, `one_shot`; diverse selection per file
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
export FREESOUND_TOKEN=...       # https://freesound.org/apiv2/apply
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
winnower list --min-interest 0.6 --type texture_loop
winnower list --commercial                   # only licences allowed for release
winnower reindex                             # rebuild index from sidecars
```

Every harvest takes `--limit` (source files to process) and `--max-duration` (seconds).
Re-running a harvest skips files already processed. If a search fails or a daily quota
runs out, the run stops with a message and keeps what it already harvested.

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
samples/{id[:2]}/{id}.flac                   the cut region
samples/{id[:2]}/{id}.json                   sidecar: the source of truth
index/samples.jsonl                          derived, rebuildable
```

## How regions are scored

All scores are 0 to 1 and stored in the sidecar.

| Score | Meaning |
| --- | --- |
| `interest` | Weighted mix of motion, modulation (0.5 to 20 Hz), spectral richness, micro-event rate, frame diversity and stereo movement. Regions under `min_interest` (default 0.35) are dropped. |
| `stationarity` | Stability of ~2 s block statistics. High for rain, low for a sweep. |
| `loopability` | Spectral match between the start and the best end point. |
| `rhythmicity` | Periodic pulses at 30 to 240 BPM. Ranks rhythmic material down. |
| `trend` | One-way movement in loudness or brightness (gestures). |
| `quality` | Silence, clipping, encoder low-pass cliffs. |

The interest weights are hand-set and checked against synthetic sounds in
`tests/synth.py` (sine, hum and white noise must fail; rain, crackle and sweeps must pass).
They are a starting point to be replaced by models learned from your ratings.

Known gap: slow continuous gestures can get split into several short segments; merging
adjacent segments with a consistent trend is a planned improvement.

## Development

```sh
pytest -q
ruff check src tests && ruff format src tests
```
