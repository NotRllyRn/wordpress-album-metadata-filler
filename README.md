# Wordpress-PostToAlbum-Script

One-time Python CLI for backfilling existing WordPress release posts with normalized custom metadata used by archive/search UI.

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

## Environment

```bash
cp example.env .env
```
and modify.

Required in `.env` or the shell:

```env
WORDPRESS_BASE_URL=https://your-site.example
LASTFM_API_KEY=your-lastfm-key
```

Required only for writes:

```env
WORDPRESS_USERNAME=api-user
WORDPRESS_APP_PASSWORD=application-password
```

## Dry Run

Dry-run is the default. It probes the WordPress REST shape, reads posts, prints planned updates, and does not write.

```bash
.venv/bin/python -m post_to_album.cli --batch-size 20 --limit 10
```

## Apply

Writes require `--apply` and WordPress application-password credentials.

```bash
.venv/bin/python -m post_to_album.cli --apply --batch-size 20
```

## Tests

```bash
.venv/bin/python -m pytest -v
```
