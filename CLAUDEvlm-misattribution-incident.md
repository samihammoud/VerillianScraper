# VLM misattribution incident — romance world (2026-09-03)

Context doc for re-describing romance world posts. Read this before touching `vlm_json` on any romance post.

## The bug

For a meaningful fraction of romance-world posts, `posts.vlm_json` (and the derived `hook`, `summary`, `visual_description`, everything downstream) describes a **different video** than the one actually stored at that row. The post's own scraped identity — `caption`, `external_id`, `handle`, `posted_at` — is correct. Only the AI-generated description is wrong.

This is not a UI bug, not a routing bug, not a clustering bug. Clustering (premise/hook/punchline) was working correctly on the data it was given — it just surfaced the corruption by grouping several wrongly-described posts together under one coherent-looking label, which is what triggered noticing it in the first place ("POV: You have a husband" hook cluster, then a premise cluster of "compilation of couples pranking each other").

## Root cause

`src/services/vision.py`'s `describe_posts()` submits a Gemini Batch API job (`_client.batches.create(model=VIDEO_MODEL, src=requests)`) and originally collected results with:

```python
responses = job.dest.inlined_responses
pairs = list(zip(requests, responses))   # trusted Gemini to return results in submission order
```

`_collect_result` then read `post_id = request.metadata["post_id"]` from the *request* side of the zip, never validating it against anything in the response. But `types.InlinedResponse` has its own `metadata` field, specifically for this kind of correlation — echoed back from whatever was set on the request. The code set `metadata={"post_id": ..., "file_name": ...}` on every request and then never read it back off the response.

A live SDK test (10-item text batch, cheap/fast) confirmed metadata *is* echoed back correctly and order *was* preserved at that scale. That doesn't clear the code — production batches run up to `CLAIM_BATCH_SIZE=2500` real video-description requests (far heavier per-item than a text echo), and Gemini's batch ordering guarantee at that scale was never actually verified before this code shipped. The observed corruption pattern is exactly what response-reordering would produce.

## Fix applied (already in the codebase)

`_collect_result` and `_collect_and_cleanup` in `vision.py` now read `post_id`/`file_name` from **the response's own `item.metadata`**, never from a positionally-zipped request. Self-checked (`python -m src.services.vision`), syntax-verified. This prevents the bug **going forward only** — it does not repair anything already written.

## Why already-corrupted rows can't be repaired, only detected + redone

Two cleanup mechanisms destroy the evidence needed to reverse-map a wrong description back to its true post:
- `_finalize_videos` deletes a post's local `.mp4` the moment it reaches a terminal state, and "has `vlm_json`" *is* that terminal condition — regardless of whether the `vlm_json` is actually correct.
- `_collect_and_cleanup` deletes the Gemini-side uploaded file once any result is collected for it, correct or not.

So for any corrupted row, both the original video and the Gemini file are already gone. There is no artifact anywhere that would let you work backward from "this text describes a spice-jar video" to "therefore it belongs to post X." **The only remedy is: detect suspect rows, clear their `vlm_json`, let `describe_posts()` redo them under the fixed code.**

## Evidence gathered (verification method, so it can be repeated)

1. **Definitive single-row proof** — `@daniellemelhanna` post `7679226855462817054`: `posts.caption` = `"wisest words by the most wonderful soul #fyp #dollyparton"`, which exactly matches the real TikTok video (confirmed by direct browser navigation). But `posts.vlm_json` on that *same row* describes "a man in a podcast studio discussing unrealistic relationship standards" — a completely different video. Since `caption` and `vlm_json` are two columns of one primary-key row, this rules out any join/query/UI explanation; the corruption is in what got written to `vlm_json` at describe-time.

2. **Cheap corpus-wide detector, no video-watching required**: compare `posts.caption` against `posts.vlm_json->>'summary'` for semantic relatedness. A caption about Scotland travel outfits, badminton, Steve Harvey quotes, spooky-season fall content, gym/bodybuilding, or parenting tips paired with a summary about "couples pranking each other" is not a borderline case — it's a clear signal.

3. **Scale check**: in one premise cluster alone ("compilation of couples interacting, teasing, and joking with each other" — 56 members), at least **10/56 (~18%)** had captions with zero topical relation to their stored summary, found by eyeballing the list. This was a conservative count — ambiguous cases weren't included.

4. **Timing**: corruption is not confined to one run. Confirmed-bad `visual_generated_at` timestamps cluster around `2026-09-02 16:14–16:51`, `18:05–18:15`, and one at `2026-09-03 00:13` — spanning at least 3-4 separate `describe_posts()` batch invocations, with confirmed-good posts interleaved on both sides (not a single before/after cutover).

## What's still unknown / not yet done

- **True corruption rate across the whole romance corpus is unmeasured.** Only ~60 posts across 2 clusters were manually checked. A scalable detector (embedding similarity or keyword-overlap between `caption` and `vlm_json.summary`/`hook`, run across all romance posts) was proposed but not built.
- **Whether other worlds are affected is unchecked.** The same `describe_posts()` code path processed every world's posts (pets, food-cooking, etc.), not just romance. This doc only covers romance because that's where it was found.
- **No decision made yet** on whether to (a) clear `vlm_json` for every romance post and fully redo, or (b) build the detector first and cherry-pick only flagged rows. Given detection is inherently incomplete (can't catch cases where a bad description happens to be topically plausible for the wrong post), (a) is the safer option if cost/time allow — (b) risks leaving silent corruption in rows that didn't get flagged.

## What re-describing needs to do

To force `describe_posts()` to redo a post under the fixed code:
```sql
UPDATE posts SET vlm_json = NULL, visual_description = NULL, visual_model = NULL,
                 visual_generated_at = NULL, visual_attempts = 0
WHERE id IN (<affected romance post ids>);
```
Then `describe_posts()` will re-claim it (`_claim_video_candidates` requires `vlm_json IS NULL`), **provided the local video file still exists** — `video_path(post_id)` was deleted for every post that ever reached `vlm_json IS NOT NULL`, so a full re-describe likely requires re-downloading video bytes first (`backfill_videos.py` — re-lists the account via `get_user_videos` for a fresh signed URL, matches back by `external_id`, re-downloads only what's missing on disk). Budget for that: some fraction of accounts/videos may no longer be available to re-fetch (deleted, private, or the account gone) by the time this runs.
