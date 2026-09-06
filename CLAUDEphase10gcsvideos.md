# Phase 10 — GCS-backed video storage + GCS batch JSONL for the VLM pass

## What's changing

Videos move from local disk (`out/videos/{post_id}.mp4`) to a GCS bucket. The
VLM pass stops uploading video bytes to the Gemini Files API and instead
registers existing GCS objects as Gemini `File`s by URI. Registration is
split into its own pass and its result is persisted on the post row — the DB
becomes the source of truth for "has this post's video been registered with
Gemini," not an in-memory list or a ledger file. The batch request itself
stops being built in-process (`InlinedRequest` list) and becomes a JSONL file
written to GCS, with `batches.create` pointed at that file.

GCP auth and the bucket are already provisioned — this is code only.

## Delete

All of this goes; nothing here survives in any form:

| in `vision.py` | why it's gone |
|---|---|
| `_client.files.upload(...)` in `_submit_upload` | replaced by `register_files`, moved to its own pass — see below |
| `CLAIM_BATCH_SIZE` and its 20GB-quota comment | replaced by a manifest-size cap, see below |
| `UPLOAD_LEDGER` / `_ledger_append` / `_recover_orphaned_uploads` | replaced entirely by the `posts.gemini_file_name` column — registration state now lives in Postgres, resumable by a plain claim query, no ledger file needed |
| `types.InlinedRequest(...)` list construction and `_client.batches.create(model=..., src=requests)` | replaced by GCS-JSONL `src` |
| `VIDEO_WORKERS` used for parallel byte uploads | no bytes are uploaded to Gemini anymore; keep a (possibly smaller) pool for the `register_files` calls |

`_await_active` is **not** deleted — it still polls a file to `ACTIVE` before
it's referenceable, it just polls a `REGISTERED`-source file now.

In `video_storage.py`:

| gone | replaced by |
|---|---|
| `VIDEOS_DIR`, `video_path()` (local path) | GCS blob path: `videos/non-described/{post_id}.mp4` |
| `store_video(post_id, data)` writing to disk | uploads bytes to the GCS bucket at that blob path |
| `delete_video(post_id)` (local unlink) | see lifecycle section — becomes a GCS copy+delete between prefixes, not a delete |

In `ingest.py`, `_fetch_video` no longer calls a disk-writing `store_video` —
same function name, new body, uploads straight to GCS. Ingest does not touch
Gemini at all, same as today — registration stays inside the VLM pass in
`vision.py`, not ingest, so ingest stays TikTok/GCS-only.

Nothing in `crawl.py`, `query_gen.py`, `routing.py`, `blob.py`,
`vision_schema.py` changes. `flatten_for_blob` and the response schema are
untouched — this phase only changes how bytes get in front of Gemini and how
the batch is submitted.

## Add

### 0. Schema — one new column, the backbone of this design

```sql
ALTER TABLE posts ADD COLUMN gemini_file_name text;  -- e.g. "files/abc123"
```

Written the instant a single `register_files` call for that post succeeds —
see step 3. This is what makes registration resumable and crash-safe without
a ledger: a post either has a `gemini_file_name` or it doesn't, and that's
always exactly true because it's a committed DB row, not an in-memory list
that dies with the process.

### 1. GCS layout

```
gs://<bucket>/videos/non-described/{post_id}.mp4
gs://<bucket>/videos/described/{post_id}.mp4
gs://<bucket>/batches/{batch_job_name}/input.jsonl
```

`post_id` (your own UUID, already the on-disk filename today) is the object
name — identity is established at write time, before anything touches Gemini.
Do not use the TikTok external video id here; `post_id` is what every other
table already joins on, `gemini_file_name` included.

### 2. `video_storage.py` — GCS-backed

- `store_video(post_id, data)` -> `bucket.blob(f"videos/non-described/{post_id}.mp4").upload_from_string(data, content_type="video/mp4")`
- `video_uri(post_id)` -> `gs://<bucket>/videos/non-described/{post_id}.mp4` (replaces `video_path`)
- `mark_described(post_id)` -> copy `non-described/{post_id}.mp4` -> `described/{post_id}.mp4`, then delete the non-described copy. GCS has no atomic move; copy-then-delete is standard. This replaces `delete_video` — same call site in `_finalize_videos`, new behavior (archive, not discard).
- `list_non_described_ids() -> set[UUID]` — one `list_blobs(prefix="videos/non-described/")` call, parse the ids back out of the object names. This is only used to answer "does a video actually exist in GCS for this post" — the same role the disk stat plays today. It does **not** track registration state; that's the new column's job.

### 3. `vision.py` — registration is its own pass, persisted immediately

Two claim queries now, not one, because "has a video" and "is registered with
Gemini" are different facts with different sources of truth (GCS listing vs.
a DB column):

```sql
-- pass A: needs registering
WHERE gemini_file_name IS NULL AND visual_attempts < 3
  AND <post_id is in the GCS non-described listing>

-- pass B: registered, needs describing
WHERE gemini_file_name IS NOT NULL AND vlm_json IS NULL AND visual_attempts < 3
```

**Pass A — register, one URI per call, persist immediately:**

```python
def _register_one(candidate: dict) -> None:
    post_id = candidate["id"]
    try:
        resp = _client.files.register_files(auth=_gcp_creds, uris=[video_uri(post_id)])
        file = resp.files[0]   # single-URI call: unambiguous, nothing to zip against input order
        db = SessionLocal()
        try:
            db.execute(update(Post).where(Post.id == post_id).values(gemini_file_name=file.name))
            db.commit()
        finally:
            db.close()
    except Exception as exc:
        logger.warning("registration failed for post=%s: %s", post_id, exc)
```

One URI per call, not a bulk `register_files(uris=[...many...])`. The
response's `files` list order relative to the input `uris` list is not
documented as guaranteed, and this codebase already has one incident
(2026-09-03, batch response ordering) from trusting an unverified order
guarantee on this same API. A single-element call has nothing to order —
correctness by construction, not by inference.

Run this over a `ThreadPoolExecutor`, same worker-pool pattern as today's
`_submit_upload`. Registration is a metadata call, not a byte upload, so it's
cheap and fast per call — parallelizing it is still worth it at volume, but
there's no upload-bandwidth argument for batching URIs into fewer calls the
way there was for videos.

**Pass B — build the batch from what's already registered:** reads
`gemini_file_name` off posts that have it, no live Gemini call needed to
rebuild `file.uri` — the Files API accepts `file_name` directly wherever
`file_uri` is accepted, or do one cheap `files.get(name=...)` per post if the
URI itself is needed and wasn't stored. (Store `gemini_file_uri` too, next to
`gemini_file_name`, if that saves a round trip later — one extra nullable
text column, decide based on whether `Part.from_uri` needs the URI or the
name works.)

`_await_active` runs over the newly-registered files in pass A before they're
usable in pass B — same shape as today, just polling `REGISTERED`-source
files.

`_delete_uploaded_file` stays, called on collect (step 4) — `client.files.delete(name=...)`
deletes the *Gemini-side registration*. Confirm empirically that this does
not touch the underlying GCS object (it shouldn't — registration is a
pointer, not a copy — but verify before relying on it).

### 4. Batch submission and collection — GCS JSONL, metadata for correlation

```python
lines = [
    json.dumps({
        "model": VIDEO_MODEL,
        "contents": [{"parts": [{"file_data": {"file_uri": gemini_file_uri, "mime_type": "video/mp4"}}]}],
        "config": {"system_instruction": SYSTEM_INSTRUCTION, **GENERATION_CONFIG},
        "metadata": {"post_id": str(post_id), "file_name": gemini_file_name},
    })
    for post_id, gemini_file_name, gemini_file_uri in pass_b_candidates
]
manifest_uri = f"gs://{bucket}/batches/{batch_id}/input.jsonl"
upload_string("\n".join(lines), manifest_uri)
job = client.batches.create(model=VIDEO_MODEL, src=types.BatchJobSource(format="jsonl", gcs_uri=[manifest_uri]))
```

**Correlation on collect uses `metadata`, not the returned `File`'s own
name/uri, and not a reverse lookup by matching the file reference against the
posts table.** `metadata` is the field the API provides specifically for this
— it's what `_collect_result` already reads today (`item.metadata["post_id"]`)
and it's unrelated to whether the request's `file_uri` came from an upload or
a registration. Keep that code as-is.

**Free correctness check, worth adding:** on collect, before writing
`vlm_json`, assert `item.metadata["file_name"] == <the post's stored
gemini_file_name>` (one indexed lookup, or just compare against a dict built
once from pass B's candidate list). If they ever disagree, something
upstream mismatched a post to a file, and you find out immediately instead of
silently attributing the wrong description to a post — same category of bug
as the 2026-09-03 incident, caught for the cost of one comparison.

**Confirm before relying on it:** that `metadata` on a GCS-JSONL request line
round-trips into the response the same way it does for `InlinedRequest`
today. If it doesn't survive that path, add `post_id` as a top-level field in
the manifest schema instead of losing the mechanism.

Reading batch output: `job.dest` for a GCS-sourced job is a GCS URI (or list
of them) rather than `inlined_responses` — download/stream the response
JSONL, one `_collect_and_cleanup` call per line, same as today's loop over
`inlined_responses`.

### 5. Replace `CLAIM_BATCH_SIZE`

Old constant capped in-flight video count against the Files API's 20GB
live-storage quota — irrelevant once bytes never leave GCS to be duplicated
into Files API storage (confirm this empirically once). The real constraint
now is the **batch JSONL manifest itself**, which Gemini caps at 2GB. Cap
pass-B candidates per pass by serialized manifest size, not video count:

```python
MANIFEST_SIZE_LIMIT = 2 * 1024**3  # 2GB, Gemini batch JSONL cap
```

Build request lines incrementally, stop adding once the running serialized
size would exceed the limit, leave the rest for the next pass — same
"unclaimed posts just wait for the next `describe_posts()` call" semantics as
today, just gated on a different resource. Pass A (registration) has no
comparable cap — it's one cheap call per post — but keep a sane worker-pool
size (`VIDEO_WORKERS`) so a single pass doesn't open thousands of concurrent
connections.

## Lifecycle summary

```
ingest    -> upload bytes to videos/non-described/{post_id}.mp4
pass A    -> for posts with a video and gemini_file_name IS NULL:
             register_files(uris=[video_uri]) one at a time
             -> write gemini_file_name (+ uri) to the post row immediately
pass B    -> for posts with gemini_file_name IS NOT NULL and vlm_json IS NULL:
             build JSONL manifest (metadata: post_id, file_name), capped at 2GB
             -> upload manifest to GCS -> batches.create(src=gcs_uri)
poll      -> job.done
collect   -> read response JSONL, match on metadata["post_id"],
             assert metadata["file_name"] == stored gemini_file_name,
             write vlm_json + visual_description
cleanup   -> client.files.delete(name=gemini_file_name) per collected post
finalize  -> mark_described(post_id) for every post that got vlm_json or hit MAX_ATTEMPTS;
             everything else stays in non-described/ for the next pass
```

## New config

`settings.py`: add `gcs_bucket: str` and whatever the GCP credentials object
needs (service account path, or rely on ADC if the environment already has
it — you said auth is set up, so wire whatever `google.auth.default()` needs
into a single place `vision.py` and `video_storage.py` both import from).

`requirements`: add `google-cloud-storage` — `google-genai` already covers
`register_files` and the batch API, but writing/listing/copying blobs needs
the storage client.

## Verify

- Round-trip `metadata` through one real GCS-JSONL batch of ~5 posts before
  trusting it at scale — this is the load-bearing assumption of the whole
  redesign, and the free assertion in step 4 is what catches it fast if it's
  wrong.
- Confirm `client.files.delete()` on a registered file does not delete the
  underlying GCS object.
- Confirm registering the same GCS URI twice (e.g. a post retried after a
  crash between pass A's DB commit and pass B) doesn't error or produce a
  second, different `gemini_file_name` for the same post — since pass A's
  claim query already excludes posts with `gemini_file_name IS NOT NULL`,
  this should only matter if a crash happens *between* the register call
  succeeding and the DB commit; decide whether that's rare enough to ignore
  or worth a idempotency check against `files.get()` first.
