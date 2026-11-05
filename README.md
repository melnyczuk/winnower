# winnower

Finds licensed audio online, keeps the aurally interesting, textural parts, and stores
each one as a FLAC file with a JSON sidecar describing it. Built for arrhythmic, textural
material: regions are found by timbral change rather than beats, and anything too uniform
(hum, room tone, plain noise) is rejected. Designed to run on a Raspberry Pi 5 or a small
server.

Background and design decisions: the research report "Sample crawler stack" (Claude Doc).

## Status (v0)

- [x] Freesound search adapter (API key; HQ OGG previews), rate limited to 60/min, 2,000/day
- [x] Licence gate: CC0/PD and CC BY allowed, NC/SA/Sampling+ kept but flagged, ND and
      unlicensed audio skipped. Attribution text stored with every sample.
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
- [ ] More sources (Internet Archive, Wikimedia Commons)

## Install

```sh
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # add ,index for LanceDB, ,s3 for buckets
cp winnower.example.toml winnower.toml
export FREESOUND_TOKEN=...       # https://freesound.org/apiv2/apply
```

On a Raspberry Pi, use 64-bit Raspberry Pi OS; every Python dependency has Linux ARM wheels.
Install `ffmpeg` (`sudo apt install ffmpeg`) to read AAC/M4A, ALAC and other formats
libsndfile can't.

## Use

```sh
winnower harvest freesound "bowed metal" --limit 20
winnower harvest local ~/Music/albums        # folders are scanned recursively
winnower harvest local ~/netlabel-ep --licence https://creativecommons.org/licenses/by/4.0/
winnower list --min-interest 0.6 --type texture_loop
winnower list --commercial                   # only licences allowed for release
winnower reindex                             # rebuild index from sidecars
```

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
