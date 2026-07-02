# Image Support TODO

Goal: let the model see repo images, view them in tabs, and embed repo images
(pinned to a commit) in its own output. All gated by a settings toggle for
models without vision support.

## Key simplification

Degradation is **not** our job to implement pixel-wise. OpenRouter/OpenAI-style
vision APIs accept a `detail` field per image part:

```json
{"type": "image_url", "image_url": {"url": "data:...;base64,...", "detail": "low"}}
```

`"low"` is a fixed ~85 tokens regardless of resolution. So "degrade further back
 in the log" just means: send the same full-res image every time, but set
`detail: "low"` for all but the most recent occurrence(s), and `detail: "auto"`
(or `"high"`) for the newest. No Pillow, no resizing, no cached variants.

## Settings

- [ ] Add `llm.vision_enabled` to `Settings.DEFAULT_SETTINGS` (default `False`).
- [ ] Expose toggle in `settings_dialog.py`.
- [ ] Single gate point: wherever we decide whether a file gets read as an
      image content block for the model vs. skipped/refused. Tab viewing and
      output embedding are independent of this flag (that's for the human,
      not the model) and should work regardless.

## 1. Model can see repo images (core)

**Design principle: the live VFS file and the conversational record are
decoupled.** The file in the VFS is just a normal (binary) file — it commits,
diffs, deletes, and opens in tabs like anything else, and a custom tool can
write one directly. The *moment* an image enters the conversation (explicit
context-add, or the model referencing it in output — see §4), we snapshot its
current bytes as a base64 data URL and store that snapshot directly in the
session JSON. No OID indirection, no blob-serving layer: the snapshot itself
is the historical record, frozen at reference time, unaffected by later edits
or deletion of the source file. This is simpler than commit-pinning and
doesn't require the git blob to stay reachable.

- [ ] VFS: add a binary write/read path so tools can produce images as
      pending files. `WorkInProgressVFS.pending_changes` is currently
      `dict[str, str]` (text only) — add a parallel bytes-based path (e.g.
      `write_bytes`/`read_bytes`) rather than overloading `write_file`.
      Check `ForgeRepository.create_tree_from_changes` — confirm/extend it to
      accept binary blobs alongside the text changes dict when committing.
- [ ] `ToolContext` (`forge/tools/context.py`): expose `write_bytes`/`read_bytes`
      so a custom tool can generate an image file directly.
- [ ] `update_context`/`add_active_file` path: today `_should_summarize` /
      `BINARY_EXTENSIONS` filters images out entirely. Add an image-aware
      branch: if `vision_enabled` and the file is an image extension, read
      bytes, base64-encode, and take the context-entry snapshot described
      above instead of routing through the text-summary path.
- [ ] Prompt manager (`forge/prompts/manager.py`):
  - [ ] New `BlockType.IMAGE_CONTENT` whose `content` *is* the base64 data URL
        itself (self-contained — no filepath lookup needed to replay it).
  - [ ] `append_image_content()` — mirrors `append_file_content()`'s
        tombstone-and-move-to-end semantics for cache optimization.
  - [ ] `to_messages()`: needs to emit multimodal content
        (`content: [{"type": "text", ...}, {"type": "image_url", ...}]`)
        instead of a plain string, for any message containing an image block.
        This changes the message shape for those turns — check cache_control
        placement still makes sense.
  - [ ] Detail-level logic: determine at `to_messages()` build time which
        image blocks are "most recent" (e.g. last N, or only images in the
        active-files set / most recent turn) → `detail: "auto"`; all earlier
        occurrences → `detail: "low"`. Applies uniformly regardless of
        whether the image came from context-add or model-embedded output
        (§4) — same block type, same rule.
  - [ ] Token estimation (`_estimate_tokens` is char-based) doesn't apply to
        images — use a fixed estimate per image (85 for low-detail; a rough
        constant like 1500 for auto/high, since real cost depends on
        resolution and we don't need to be exact for the mood bar).
- [ ] `LLMClient`: no changes expected — payload already passes `messages`
      through untouched, so multimodal content blocks should pass through
      fine as long as `PromptManager` builds them correctly. Verify OpenRouter
      accepts the standard OpenAI vision shape for the configured model.

## 2. Compaction drops images

- [ ] When `compact_messages(from_id, to_id, ...)` tombstones a range, any
      `IMAGE_CONTENT` block in that range is dropped outright (no low-detail
      fallback survives compaction — same as other compacted content).

## 3. View an image in a tab

- [ ] `BranchWorkspace`: add `get_file_bytes(filepath)` so UI doesn't reach
      into `vfs.base_vfs` directly (ownership rule — ask the owner).
- [ ] New `ImageViewerWidget` (QLabel/QPixmap, minimal zoom/fit-to-window).
- [ ] `branch_tab_widget.py` `open_file()`: extend the existing `.md` →
      `MarkdownPreviewWidget` dispatch pattern with an image-extension branch
      that creates `ImageViewerWidget` instead of `EditorWidget` (no text
      editor needed for binary images).
- [ ] Update `_find_tab_index()` and `_on_tab_close_requested()` for the new
      wrapper type, same as the markdown wrapper is handled.

## 4. Model embeds repo images in output

No custom URL scheme, no blob-serving, no commit-oid tracking needed — this
reuses the exact same snapshot mechanism as §1.

- [ ] Model writes normal markdown `![alt](path.png)` referring to a repo
      file, exactly as it would reference any other file by path elsewhere.
- [ ] At **message-finalization time** (when the assistant turn is finalized
      in `live_session.py`, not at render time), scan the assistant message
      text for image references that resolve to an existing VFS file. For
      each one found, read current bytes, base64-encode, and record it as an
      `IMAGE_CONTENT`-bearing attachment associated with that message (same
      snapshot shape as §1) — keyed by the literal path text so the renderer
      can splice it back in. **Leave the visible markdown text itself
      unchanged** (`![alt](path.png)`) — don't bake the data URL into the raw
      message string, or every future replay of that message's text into
      later prompts would carry the full base64 blob permanently.
  - [ ] If the path doesn't resolve to an existing image file, leave the
        markdown text untouched (no fallback guessing).
- [ ] `render_markdown()` in `tool_rendering.py`: when rendering a message
      that has attached image snapshots, substitute the matching `![alt](path)`
      occurrence's `src` with the stored data URL before/after markdown
      conversion. No VFS/git access needed at render time — it's all in the
      message's own attached data.
- [ ] Because these are ordinary `IMAGE_CONTENT`-shaped snapshots, they ride
      the same detail-degradation (§1) and compaction-drop (§2) rules as any
      other image in context — an old embedded output image just becomes
      `detail: "low"` and eventually gets dropped on compaction, same as one
      added via context.

## Phasing

1. Core vision (settings toggle, byte-level VFS reads, image content blocks,
   multimodal messages + detail levels, `update_context` image support).
2. Compaction drop.
3. Tab viewer.
4. Embedded output images (scheme handler + finalization rewrite pass).

## Open questions

- How many "most recent" images stay at `detail: auto` — just the latest
  turn's images, or a fixed count? Simplest: only images still attached to
  files in the *current* active-files set get `auto`; anything tombstoned/
  superseded gets `low`.
- Do we need a max total image count/size per turn (cost guard), independent
  of the token-budget logic used for summaries?
