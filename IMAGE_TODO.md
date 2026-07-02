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

- [ ] VFS: expose byte-level reads uniformly. `GitCommitVFS` already has
      `read_file_bytes`; `WorkInProgressVFS` needs an equivalent that checks
      pending changes (pending images are out of scope for now — images are
      read-only inputs, not AI-writable).
- [ ] `update_context`/`add_active_file` path: today `_should_summarize` /
      `BINARY_EXTENSIONS` filters images out entirely. Add an image-aware
      branch: if `vision_enabled` and the file is an image extension, read
      bytes + base64-encode instead of routing through the text-summary path.
- [ ] Prompt manager (`forge/prompts/manager.py`):
  - [ ] New `BlockType.IMAGE_CONTENT`.
  - [ ] `ContentBlock` needs to carry: filepath, base64 data, mimetype, and
        **blob oid** (content-addressed, same key pattern as the existing
        summary cache in `SessionManager`).
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
        occurrences → `detail: "low"`.
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

## 4. Model embeds repo images in output, pinned to a commit

- [ ] Register a custom `QWebEngineUrlSchemeHandler` for e.g.
      `forge-image://<blob_oid>/<name>` that serves raw bytes straight from
      the git odb by blob oid. Blobs stay reachable (and thus alive, ungced)
      as long as they're reachable from *some* commit, so this is stable even
      after the source file is later edited or deleted.
- [ ] Model writes normal markdown `![alt](path.png)` referring to a repo
      file. At **message-finalization time** (when the assistant turn is
      finalized in `live_session.py`, not at render time), rewrite any such
      image reference in-place: resolve `path.png` against the VFS at the
      current commit, get its blob oid, rewrite the markdown URL to
      `forge-image://<blob_oid>/path.png`. This bakes in "which commit" at
      the moment the model referenced it.
  - [ ] If the path doesn't resolve to an existing image file, leave the
        markdown text untouched (no fallback guessing).
- [ ] `render_markdown()` in `tool_rendering.py`: no lookup needed at render
      time — just needs the `forge-image` scheme registered on the page/view
      so `<img src="forge-image://...">` resolves.

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
