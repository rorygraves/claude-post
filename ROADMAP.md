# ClaudePost Roadmap

Improvements to the email MCP flow, ranked by value ÷ cost. Derived from real
session usage. Status legend: ✅ done · 🚧 in progress · ⏳ planned · 🔎 needs investigation.

## Guiding principle: keep collections small and ephemeral

Collections hold **volatile IMAP UIDs** (folder-scoped, and they change when a
message moves). Two consequences shape this roadmap:

- **Do not cache collections to disk.** Persisting them across restarts means
  acting on stale UIDs later — the exact silent-wrong-message bug class already
  fixed in move/delete. It also adds serialization/cleanup/staleness cost.
- **Attack the collection cap at the source**, not by growing or persisting the
  store: push counting/aggregation/bulk work server-side so fewer and smaller
  collections are created. In-memory LRU (done) + a TTL (planned) handle the rest.

## Tier 1 — quick wins

- ✅ **#4 `mail-count(folder, filters)`** — count-only, creates no collection.
  Reuses the existing `_count_emails()` primitive. Removes the `max_results=1`
  hack used just to read `total_available`, and relieves the cap. Shipped
  alongside #2.
- ✅ **#6a fetch truncation** — `mail-fetch` now defaults to `limit=None` (return
  every row), bounded by a loud `FETCH_ROW_CAP` (1000). Any truncation surfaces an
  explicit `warning` plus `returned`/`total_rows`, never a silent `"100 of 101"`.
- ✅ **#6b transform param discoverability** — `mail-transform`'s `parameters`
  description now documents each operation's parameter shape (keys, types,
  defaults), which flows into the tool's JSON schema via `parse_docstring_params`.

## Tier 2 — high value, more design

- ✅ **#1 Stable Gmail IDs** — `mail-move` / `mail-delete` / `mail-get-content`
  now accept `gmail_msgid(s)` (`X-GM-MSGID`), resolved to the current UID
  server-side via `UID SEARCH X-GM-MSGID <id>` in the target folder. Stable across
  folders → eliminates the stale-UID bug class (fixes the *cause*; the move/delete
  not-found guard already shipped fixes the *symptom*). Results are reported back in
  the identifier space the caller supplied. Bulk get-content by gmail id is a
  fast-follow (single is supported now).
- ✅ **#2 `mail-aggregate`** — server-side group-by (sender / recipient / date)
  over a folder+filter, returning a small top-N table and **no collection**.
  Normalizes sender/recipient to a bare address (which also feeds #7). Replaces
  paging thousands of rows into context to count them — the biggest token saver.
- ⏳ **#3 Bulk ops by `collection_id` / query** — let move/delete act on a
  collection or a search directly, removing the fetch→echo→resend ID round-trip.
  **Caveat:** a collection stores search-time UIDs that go stale after a move, so
  this must re-resolve through the not-found-reporting path, not trust stored IDs.
  Largely subsumed by #1; build after it.

## Tier 3 — smaller / conditional

- ⏳ **#7 Exact sender match** — IMAP `FROM` is substring-only by spec; add an
  `exact=true` flag that post-filters on the parsed bare address. Partly obviated
  once #2 normalizes addresses.
- ⏳ **#5 Collection TTL** — auto-expire idle collections in memory, on top of the
  LRU eviction + `mail-clear` / `mail-drop` already shipped. Better addressed by
  Tier 1 reducing collection creation than by more store machinery.
- ✅ **#8 Folder-listing escaping** — the `split('"')` parser was correct for
  standard Gmail output but garbled or silently dropped four non-standard response
  shapes: names with escaped `\"`/`\\` (truncated), literal (`{n}`) names imaplib
  returns as tuples (dropped), unquoted-atom / `NIL`-delimiter names (dropped), and
  non-ASCII modified-UTF-7 labels (shown as mojibake — the likely source of the
  reported garble). Replaced all four `split('"')` sites with one tested
  `parse_list_response_line` that handles quoted/literal/atom names, unescapes IMAP
  escapes, and decodes modified UTF-7 for display while keeping the wire form for
  commands. Regression tests cover every case.

## Already shipped

- ✅ Collection store: LRU auto-eviction, `mail-clear`, `mail-drop` (was 100-cap
  hard-fail with no eviction).
- ✅ move/delete no longer report false-positive success on stale UIDs — they
  resolve against a `UID SEARCH` and report `affected` vs `not_found`.
- ✅ Multi-account support via `accounts.toml`.
