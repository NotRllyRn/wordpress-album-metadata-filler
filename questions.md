# Questions — blocks before implementation begins

> **Historical context:** This records pre-implementation discussion and may contain superseded names or proposals. Plans 00–04 are the current canonical contract.

Everything in `plan.md` is now in sync with your answers to my first round. These are the new things I need to know before writing code. Each question is **explicit about what I'd do if you say nothing** so you can answer `yes-take-the-default` if you want.

---

## Q1 — Release-type taxonomy semantics

**The question.** Some posts will legitimately need **multiple** `release_type` taxonomy terms at once. For example:

- A post that's a real album-typed release AND is also marked as a Relisten → `[Album, Relisten]`
- A release typed as Album BUT not officially out yet AND it's a second-listening → could be `[Album, Relisten]` (still real type = Album)

The new SCF has an `unreleased` true_false field. There's no `is_relisten` true_false field anymore — `previous-listen-posts` was deleted. So the CLI can't reliably tell whether a post is a relisten from the SCF fields alone.

**What I need from you.** Which of these is your model for the `release_type` taxonomy ordering:

- **(a)** Each post gets exactly one `release_type` term: the Spotify-derived heuristic output (`Album` / `EP` / `Single` / `Compilation`). Relisten/Unreleased statuses are tracked purely in SCF metadata (`music_notes`, `unreleased`, plus implicit detection via the existing `relisten` category).
- **(b)** Each post gets the heuristic term AND additionally `Relisten` or `Unreleased` if the corresponding ACF flag is true — leading to `[Album, Relisten]` style lists. The heuristic term stays first so it carries semantic meaning; the marker term is informational.
- **(c)** Same as (b) but the marker term is **only** added when the existing `category` of the post is `Relisten` (id=93) or `Unreleased` (id=200), regardless of any ACF flag.

→ **My default if you don't answer:** `(a)`. Reasoning: vision.md says "Relisten and Unreleased already have their own fields in the metadata scf so that's where you will mark it" — this is a strong hint that you want them in SCF, not in the release_type taxonomy. But I want to verify because the term naming in the new SCF export schema doesn't include an obvious `is_relisten` field.

**How to spot the right answer.** Look at your existing posts that are in `Relisten` (category=93). Look at one in WP admin and tell me what its SCF fields look like. If they have `is_relisten=true` somewhere I missed, great — tell me. Otherwise the policy in (a) is sensible.

A: choose option a.

---

## Q2 — The new `album` custom post type

**The question.** When I queried WP tonight, the REST root returned a new post type registered: `album` (label "Albums"). It exists in WP (`GET /wp/v2/album` returns 200) but **0 posts** are filed under it — all 807 of your posts are still under `post`.

**Why I need to ask.** Possibilities:

- (i) You registered it as a future migration target and want this CLI to **move** the 807 posts from `post` to `album` post type as part of the run.
- (ii) You registered it by accident / for future use, and the CLI should **ignore** it (keep everything under `post`).
- (iii) You intend to migrate manually later; the CLI just writes SCF + taxonomies on `post`, and the `album` post type remains empty until you do that migration.

→ **My default if you don't answer:** `(iii)`. The CLI will write to `post` only and never touch `album`. Migrating post types mid-run sounds risky and chain-breaks the existing 7 categories filter in WP admin.

**What I'd need from you to do option (i).** Confirmation that `album` inherits the same custom taxonomies (`artist`, `genre`, `release_type`) and that the categories from `post` should be re-seeded on the new post type. Also whether the `featured_media` should be preserved (per Q7 the CLI doesn't touch covers, but moving a post to a new type can lose the cover attachment unless WP is configured to share media).

A: for now, we are not going to move anything to album. Just follow your default. write everything to post and don't touch album.

---

## Q3 — How is `listen-count` actually determined?

**The question.** Per `scf-export-field-meanings.md`:

> *listen-count-index: the number index of how many times this release has been listened too. Basically this will be 1 for most released except for the ones where I listened to an album twice, where the 2nd blog post would have this number at 2 instead.*

That's a **relisten counter**, not a listen counter measured from your history. So a post with listen-count=2 means it's the 2nd blog post you wrote about the same album. The challenge: when the CLI runs for the first time, **every** post is at 1 by default, because the CLI has no way to know which posts are relistens of each other.

**What I need from you.** Which strategy do you prefer:

- **(A)** CLI defaults to `listen-count=1` on every post. You then manually bump the 2nd, 3rd, etc. iteration of relistens by hand in WP admin — same as today.
- **(B)** CLI attempts relisten detection: after writing `spotify_album_id`, before finalising, group ALL posts by `spotify_album_id`. The first one is 1, the next chronological post (by `post.date`) with the same `spotify_album_id` is 2, etc. This is one extra pass at the end of the run.
- **(C)** CLI looks for a matching relisten indicator elsewhere — e.g., a `relisten` category (id=93) on a post — and bumps +1. Crude, but matches WP taxonomy already.
- **(D)** CLI never touches `listen-count` at all; you fill it manually.

→ **My default if you don't answer:** `(A)`. Reasoning: it's the simplest policy and avoids accidental renumbering if your matching confidence isn't perfect. (B) is more "correct" but breaks idempotent re-run — re-running it can re-number if the order of API responses changes.

A: for now just set it to 1 for everything. I will change this stuff manually later.

---

## Q4 — Are SCF `_source` companion fields actually attached when REST writes to ACF?

**The question.** I said earlier "rely on SCF source feature, don't write `_source` ourselves." But empirically I'm unsure: SCF's built-in "Source" attribution in the WP admin UI tracks a source per-field. **The question is whether setting a field via REST POST to `/wp/v2/posts/{id}` (`acf: { music_release_date: "2014-09-29" }`) ends up showing "Spotify" as the source in the SCF meta box UI, or whether it shows blank / "—".**

The risk: after our `--apply` pass, every populated field shows blank source in SCF UI even though we did attribute them. Then SCF's UI will look broken.

**What I need from you.** Would you like me to:

- **(i)** Test once on a single throwaway post before bulk-writing — verify empirically, then decide whether to write `_source="spotify"` ourselves.
- **(ii)** Just write `_source="spotify"` defensively for every ACF field we set, sidestepping the question.
- **(iii)** Don't touch `_source` and don't test — accept that the SCF UI shows "—" for fields we wrote.

→ **My default if you don't answer:** `(i)`. Test on one post, log the result, only ever write one field if necessary, document in plan.

**Why I lean toward empirical test:** SCF updates have been breaking the Source feature across versions. I'd rather know than guess.

A: follow your default. 

---

## Q5 — Genre maximum count & Spotify/LFM priority

**The question.** `music_mood_tags` is documented as "the top three tags on last.fm." So mood tags come from Last.fm only, max 3, with blocklist. But `genre` (a separate taxonomy) is similar — it wants accurate, recent genre tags. The `vision.md` says "find that same release on LastFM … use the top 3 tags for the genre."

The ambiguity: Spotify's `album.genres[]` can return e.g. `[indie pop, indie folk, stomp and holler]` which is already 3 spotify-curated genres. Last.fm `tags.tag[]` may return 20+ tags for the same album.

**What I need from you.** For the **genre** taxonomy specifically:

- **(I)** Top-3 priority = Spotify first. If Spotify returns N≥3, take first 3. If N<3, pad with Last.fm (after blocklist). Total cap = 3.
- **(II)** Spotify + Last.fm merged, deduped, top-3 by some weighting (Spotify > Last.fm). Total cap = 3.
- **(III)** Last.fm only, top 3, with blocklist. Spotify `genres[]` ignored.

→ **My default if you don't answer:** `(I)`. Reasoning: Spotify-cleaned genres (curated by Spotify editors) outrank fandom tags. Blocklist applies to Last.fm pad only.

Note: this is independent from `music_mood_tags`, which is Last.fm only by spec. That's not changing.

A: DO NOT use spotify genres. they are unreliable. Only use LAST FM. Number III

---

## Q6 — Alternative genre source (MusicBrainz, Deezer, etc.)

**The question.** You said in Q4:

> *Yeah you can have that tag blocklist. I want real genre tags. … Suggest an improvement to use another platform if you think it would be better to use another platform.*

You also said *"if a music album was released 50 minutes ago, it should already exist on the platform."* Last.fm is slow on new releases because it's user-driven — a brand-new album probably has no tags yet, and you end up with garbage or nothing.

**Alternatives I considered:**

- **MusicBrainz** — community-curated, immediate (releases appear within hours). Tags are clean but sparse. Per-MB-release tag list is small (5–10 typically). Free API.
- **Deezer API** — has `genres[]` for every release immediately, but the values are broad ("Pop", "Hip-Hop/Rap", "Alternative"). Good for new releases. Free API key, 50 req / 5s.
- **Apple Music / iTunes Search API** — has `genre` (single string), free no-auth. Low resolution.
- **Discogs** — has `Style` and `Genre` per release, free API. Slow on new releases.

**What I need from you.** For `genre` and `music_mood_tags`, where should the CLI look?

- **(A)** Last.fm only (current plan).
- **(B)** Spotify + Last.fm only (current plan §2c).
- **(C)** MusicBrainz + Last.fm (cleaner tags, faster on new releases; no Spotify genre use).
- **(D)** Deezer + Last.fm (broadest genres, fastest on new releases).
- **(E)** Multi-source fallback chain: Spotify first → MusicBrainz second → Last.fm third.

→ **My default if you don't answer:** `(B)`. Reasoning: you've already wired Spotify into the match flow; piggybacking on it costs nothing. Adding MusicBrainz requires a separate lookup round-trip keyed on artist + album name. If you tell me the brand-new-release problem is real and you've hit it before, switch to `(D)`.

A: Last FM only for now.

---

## Q7 — Spotify OAuth flow specifics

**The question.** You said *"yeah the program is going to get those values it self by making me open a link and giving it the necessary data. Basically do the oauth flow to get those values that you need upon running the cli and save it locally somewhere."*

That's the **Authorization Code flow** with PKCE, which requires:

1. A registered Spotify app with a **redirect URI** — typically `http://127.0.0.1:8888/callback` for a local CLI.
2. A **client ID** and **client secret** (or PKCE-only for public clients).
3. A user clicking "agree" once on first run; CLI captures the `code` from the redirect, exchanges it for `access_token` + `refresh_token`, saves the `refresh_token` to `spotify_tokens.json` (gitignored).

**What I need from you.** Specifically:

- **(P1)** Do you already own a Spotify app registration (have a `SPOTIFY_CLIENT_ID` / `SECRET` pair), or do I need to register one for you?
- **(P2)** If I register one: what name should the app be? What redirect URI should I configure (e.g., `http://127.0.0.1:8888/callback`)? Or do you have a preference (e.g., a real URL on your domain)?
- **(P3)** Music metadata only needs no user scopes, **but** Spotify requires Authorization Code (not Client-Credentials) for any user-facing flow that issues a refresh token. So this CLI will operate on **your** personal Spotify account (your library, your playlists if you ever expand). Are you OK with that, or do you want me to register as a separate "developer account"-type user that exists purely for app use?
- **(P4)** Scopes: do you want `user-library-read` or `playlist-read-private` (in case we ever want to read your listening history)? Or no extra scopes (catalog-only is enough)?

→ **My default if you don't answer:** register the app under name "Wordpress-PostToAlbum-Script CLI", redirect URI `http://127.0.0.1:8888/callback`, PKCE-only, no extra scopes (catalog-only). The CLI will print a one-time URL on `--auth`, run a tiny `http.server` on port 8888 to capture the code, save the refresh token to `spotify_tokens.json`. Subsequent runs auto-refresh. If you already have an app, point me at the env vars and I'll skip the app-creation step.

**Heads-up:** Spotify's Web API tokens only last 1 hour. The refresh token is long-lived (~6 months of inactivity before Spotify refuses to refresh). CLI should always check `expires_at` before each API call and refresh if < 60 s remaining.

A: NVM. I gave you the client id and secret in the .env file.

---

## Q8 — Will the Albums custom post type taxonomies be inherited?

**The question.** Strongly coupled to Q2. If we ever migrate posts from `post` to `album`, the `artist` / `genre` / `release_type` taxonomies need to be registered against the `album` post type as well as `post` so they show up on albums.

**What I need from you.** (Skip this question if you answered `(iii)` to Q2.) Is the `album` post type already set up to use the custom taxonomies, or do I need to add them via the SCF import / a plugin?

→ **My default if you don't answer:** the `album` post type is in the new SCF export, but only its labels are defined — no taxonomy bindings. I'd need to add taxonomy bindings via SCF or PHP. **Skip this question unless Q2 answer is (i).**

A: Skip.

---

## Q9 — Should `music_listened_at` be filled when the post has it already but is meaningless?

**The question.** `music_listened_at` per the field meanings doc is "when I listened to the album (copy from when the blog post was posted)." Today 10 posts have it filled:

- `music_listened_at`: `20260617` format (YYYYMMDD, no dashes — that's a Debian date-encoded string, weird)
- The blog post's `post.date` is `2026-07-03T17:43:38` ISO format.

So there are two ways to read this:

- (a) The CLI fills `music_listened_at` from `post.date` for all 797 currently-empty posts. Doesn't touch the 10 that already have it.
- (b) The CLI sees the existing `20260617` value as a leftover artifact from an earlier script and overwrites everything with the canonical `post.date`.

→ **My default if you don't answer:** `(a)`. Reasoning: the field meanings doc says "copy from when the blog post was posted" — suggests auto-fill is encouraged and any value you personally set should be respected. The `20260617` strings are not broken; just non-canonical. Re-formatting those into ISO would be a destructive change.

**Sub-question I should also ask but combining:** is the date format of `music_listened_at` truly `YYYYMMDD` (no dashes)? WP `date_picker` stores ISO `YYYY-MM-DD` typically, but ACF's date picker can be configured for `YYYYMMDD` too. Without knowing the SCF configuration, I will write ISO (`2026-07-03`). Will rewrite from existing `20260617` → `2026-07-03` if needed.

→ **My default:** write ISO. Don't transform existing strings.

A: choose option a.

---

## Q10 — Idempotent re-run depth

**The question.** Per your Q14 answer: "yeah you can skip posts that have already been filled out." Current skip-rule: if `spotify_album_id` AND `music_total_tracks` are both populated, skip.

The ambiguity: a post might be **partially** filled (e.g., `spotify_album_id` filled by a prior run but `lastfm_release_id` empty because Last.fm returned no match that time). Should a re-run refill empties, or skip the post entirely?

**What I need from you.** Which re-run policy:

- **(α)** Skip the entire post if `spotify_album_id` is filled (cheap, fast, but leaves gaps if prior run had gaps).
- **(β)** Skip the post if EVERY auto-fillable field is filled (slower but fills gaps). Re-runs always fill what's missing.
- **(γ)** Always process but only update fields that are currently empty (don't overwrite an existing value with a possibly-different new value). Most "correct" but most API calls.

→ **My default if you don't answer:** `(β)`. Reasoning: matches your Q14 intent ("skip posts that have been filled out" — plural posts, plural fields). The cost is one extra summary-pass to compute "is this post fully filled?" before deciding to skip.

A: choose option b.

---

## Q11 — Failure mode for mass empty Last.fm tag lists

**The question.** Live test showed: `album.getinfo` returns `tags.tag[]` empty for some albums (Sabrina Carpenter — `toptags` empty, `tags` empty). Older albums (Radiohead — Kid A) return useful tags. New albums (Sabrina Carpenter — Short n' Sweet) often return empty.

**What I need from you.**

- **(a)** If Last.fm returns no useful tags, **leave `music_mood_tags` empty** (don't fill junk, don't error). CLI logs the failure.
- **(b)** If Last.fm returns nothing, **fall back to Spotify `album.genres[]`** (top 3, with blocklist).
- **(c)** If both fail, leave empty. CLI logs a warning.
- **(d)** Write a stub like `mood: "untagged"` so the field isn't `null`.

→ **My default if you don't answer:** `(c)`. Leave empty, log a warning. Don't pollute the field with garbage.

A: Only try to use last fm tags. If nothing happens then log a CLI warning like option c.

---

## Q12 — What exactly is logged when a post fails to match?

**The question.** When Spotify returns no usable candidate, we dump to `out/unresolved.json`. The user said "dump to a unresolved.json file for manual review. Your default sounds good." OK — confirmed. But what should the CLI print to stderr at the time of the failure?

Specifically:

- **(X)** One-line `WARN: post 1234 — no Spotify match for "Heartbreak City" / Martin Gébele. See unresolved.json.` per failure.
- **(Y)** Verbose dump of top-5 Spotify candidates with scores, so user can intervene without opening unresolved.json.
- **(Z)** Silent to console, only the unresolved.json entry. (Worst — hard to spot in 807 runs.)

→ **My default if you don't answer:** `(X)`. Stderr WARN per failure. Top-5 candidates stay in `unresolved.json` only (don't spam the console).

A: use the default option.

---

## Q13 — Album-art-picker plugin + CLI competition

**The question.** There's a currently-installed WordPress plugin `Album Art Picker (Spotify) V2` (`/Album Art Picker (Spotify) V2/spotify-album-art-picker.php`). If the CLI writes to the post title / featured_media (which we agreed NOT to do), the plugin's behaviour could conflict. Even if we don't touch title/media, the plugin re-reads SCF meta on save (probably). Please confirm:

- **(i)** Plugin is benign: re-reads on post save, doesn't fight the CLI.
- **(ii)** Plugin will re-process our PATCH and reset some fields we just set. Not OK.
- **(iii)** Plugin is being kept around for a different workflow, and is fine to leave running.

→ **My default if you don't answer:** `(iii)`. Reasoning: vision.md explicitly says "ignore the talk about wordpress because another analysis gives you this" for the album-art-picker plugin — so it's not part of the CLI's contract. The CLI doesn't touch any field the plugin owns. (Title, featured_media are owned by plugin. CLI writes: acf meta, taxonomies, categories.)

A: use the default. does not modify title or featured_media.

---

## Q14 — WP REST applied to PATCH / POST

**The question.** The existing tracker analysis says WP REST accepts both POST and PATCH for `/wp/v2/posts/{id}`. Per my cheat sheet earlier I wrote "POST (POST is conventional per analysis.md)".

Quick empirical question — when I do a `POST /wp/v2/posts/{id}` with a body containing `acf: {...}` and `artist: [id1, id2]`, ACF fields update but **do taxonomies update too** in that single POST? Some WP configs only update `meta` from POST and ignore the taxonomy arrays.

**What I need from you.** No — this is for me to test. I'll log the request and response of one throwaway post, document the answer in plan.md, then propose if any additional taxonomy-rest-update call is needed.

But: if you have **prior knowledge** that taxonomies update only with `?taxonomy=…` URL params or are not handled by `POST /wp/v2/posts/{id}`, share it now — saves a test round-trip.

→ **Action:** self-test before apply. Default flow does POST with all of `{acf, categories, artist, genre, release_type}` in one body, watch response shape.

A: figure this out and run a test for your own information.

---

## Q15 — Final sanity check on the run command

**The question.** Have I got the actual invocation right? Can you confirm a one-liner signature like:

```
python post_to_album.py run --all --apply
python post_to_album.py run --limit 50 --dry-run
python post_to_album.py run --offset 500 --limit 100 --apply
python post_to_album.py auth    # one-time Spotify OAuth
python post_to_album.py stats
python post_to_album.py fuzzy "Heartbreak City" "Martin Gébele"
```

What flags do you want (rename to anything):

- `--all` vs. no flag (treat no flag = all)
- `--limit N` and `--offset M`
- `--dry-run` / `--apply` vs. a separate `plan` subcommand
- Optional: `--post-ids=12,34,56` for explicit-pick mode
- Optional: `--category=ep` for category filter
- Optional: `--only-empty-field=music_release_date` for targeted-fill mode

→ **My default if you don't answer:** `--all`, `--limit N`, `--offset M`, `--dry-run` and `--apply` (mutually exclusive, one required), with `--only-empty-field=…` as a power-user flag. No `--post-ids`. `--category=…` not implemented in v1.

A: that sounds good. use the default.

---

## Summary of new questions vs old (answered)

| # | Status |
| --- | --- |
| Old Q1 (release_type vs category) | ✓ Migrate to release_type taxonomy + keep categories mirrored (option C) |
| Old Q2 (match confidence field) | ✓ Removed entirely |
| Old Q3 (`_source` companion) | ✓ Don't write; verify SCF behavior empirically — see **Q4** |
| Old Q4 (Last.fm blocklist) | ✓ + MusicBrainz/Deezer question — see **Q6** |
| Old Q5 (multi-artist split) | ✓ 1:1 pass-through |
| Old Q6 (`music_source`) | ✓ Field gone; moot |
| Old Q7 (cover image) | ✓ Don't touch |
| Old Q8 (cover alt text) | ✓ Don't touch |
| Old Q9 (unreleased posts) | ✓ Treat like any other |
| Old Q10 (Spotify app) | ✓ OAuth user-flow — see **Q7** |
| Old Q11 (extended quota) | ✓ Stay in dev mode |
| Old Q12 (output location) | ✓ `./out/` |
| Old Q13 (concurrency) | ✓ Single thread |
| Old Q14 (idempotent re-run) | ✓ See **Q10** for granularity |
| Old Q15 (unresolved) | ✓ See **Q12** for logging shape |

New questions blocking implementation: Q1, Q2, Q3, Q4, Q5, Q6, Q7, Q8, Q9, Q10, Q11, Q12, Q13, Q14, Q15.

**Highest priority to unblock coding first:** **Q7** (Spotify OAuth setup) and **Q1** (release_type semantics). After that, **Q5, Q6, Q10** are needed before the first `--apply` run.
