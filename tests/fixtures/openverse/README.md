api.openverse.org sits behind a Cloudflare challenge from the network these fixtures were
made on, so they're built from the upstream example responses in
WordPress/openverse `api/api/examples/audio_responses.py` (same field names and types:
`duration` in milliseconds, `tags[].name`, `license` as a short code). Re-record with
`curl 'https://api.openverse.org/v1/audio/?q=rain&page_size=3'` when reachable.
