# Image Support TODO

Goal: let the model see repo images, view them in tabs, and let tools/model
embed repo images in output for the *user*. All model-facing behavior is
gated by a settings toggle for models without vision support.

## Two entirely separate mechanisms — don't conflate them

1. **Context mechanism** (§1): a human or a tool explicitly adds an image file
   to `active_files`, exactly like a text file. **Always full quality, always
   current.** No permanent record, no degradation — cost is controlled the
   same way it is for any large file: the model (or user) removes it from
   context when it's no longer needed. No Pillow involved.
2. **Output embedding** (§4): the model/tool references a repo image in its
   own chat output, for the *user's* benefit. This reference becomes a
   **permanent** part of conversation history (replayed into every future API
   request forever), so unlike §1 it cannot be full quality without eventually
   bloating every request. Fix: store two variants at embed time, one full
   quality for the user's rendered view, one Pillow-degraded for what actually
   gets replayed to the model.

Don't reuse one code path for both — they have opposite quality/lifetime
tradeoffs.

## Settings

- [ ] Add `llm.vision_enabled` to `Settings.DEFAULT_SETTINGS` (default `False`).
- [ ] Expose toggle in `settings_dialog.py`.
- [ ] Single gate point: `update_context`/`add_active_file`, when the file is
      an image — if `vision_enabled` is off, adding an image file to context
      should fail/refuse clearly (no silent no-op) rather than pretend to
      succeed. Tab viewing (§3) and output embedding (§4) are unaffected by
      this flag — that's for the human, not the model.

## 1. Context mechanism: model sees a repo image (full quality, live)

- [ ] VFS: add a binary write/read path so tools can produce/read images as
      pending files. `WorkInProgressVFS.pending_changes` is currently
      `dict[str, str]` (text only) — add a parallel bytes-based path (e.g.
      `write_bytes`/`read_bytes`) rather than overloading `write_file`.
      Check `ForgeRepository.create_tree_from_changes` — confirm/extend it to
      accept binary blobs alongside the text changes dict when committing.
- [ ] `ToolContext` (`forge/tools/context.py`): expose `write_bytes`/`read_bytes`
      so a custom tool can generate an image file directly.
- [ ] `update_context`/`add_active_file` path: today `_should_summarize` /
      `BINARY_EXTENSIONS` filters images out entirely. Add an image-aware
      branch: if the file is an image extension and `vision_enabled`, read
      **full-quality** bytes, base64-encode directly (no Pillow, no
      resizing), and add it as a live block (see below). If `vision_enabled`
      is off, refuse with a clear error.
- [ ] Prompt manager (`forge/prompts/manager.py`):
  - [ ] New `BlockType.IMAGE_CONTENT` whose `content` is the current
        full-quality base64 data URL.
  - [ ] `append_image_content()` / `remove_image_content()` — mirrors
        `append_file_content()`/`remove_file_content()`'s tombstone-and-
        move-to-end semantics exactly. `file_was_modified()`-equivalent:
        if an image file in context changes on disk, refresh the block the
        same way a text file's content is refreshed.
  - [ ] `to_messages()`: emit multimodal content (`content: [{"type": "text",
        ...}, {"type": "image_url", "image_url": {"url": ...}}]`, no
        `detail` override — let the API default (`auto`) apply, since these
        are meant to be inspected at full fidelity) for any message
        containing an image block.
  - [ ] Token estimation (`_estimate_tokens` is char-based) doesn't apply to
        images — use a rough fixed estimate (e.g. ~1500 tokens) for mood bar
        purposes; real cost depends on resolution and we don't need to be
        exact.
- [ ] `LLMClient`: no changes expected — payload already passes `messages`
      through untouched, so multimodal content blocks should pass through
      fine as long as `PromptManager` builds them correctly. Verify OpenRouter
      accepts the standard OpenAI vision shape for the configured model.
- [ ] Removing an image from context (`remove_active_file`) tombstones its
      block exactly like removing a text file.

## 2. Compaction

- [ ] Context-mechanism images (§1) are **not** touched by `compact_messages`
      — they're removed the same way any active file is removed
      (`remove_active_file`), independent of message-range compaction.
- [ ] Output-embedded images (§4) that live inside a compacted
      assistant-message range are dropped along with the rest of that
      message's content when it's compacted.

## 3. View an image in a tab  ✅ DONE

Always full quality — this is pure UI, unrelated to either mechanism above.

- [x] `BranchWorkspace`: add `get_file_bytes(filepath)` so UI doesn't reach
      into `vfs.base_vfs` directly (ownership rule — ask the owner).
- [x] New `ImageViewerWidget` (QLabel/QPixmap, minimal zoom/fit-to-window).
- [x] `branch_tab_widget.py` `open_file()`: extend the existing `.md` →
      `MarkdownPreviewWidget` dispatch pattern with an image-extension branch
      that creates `ImageViewerWidget` instead of `EditorWidget` (no text
      editor needed for binary images).
- [x] Update `_find_tab_index()` and `_on_tab_close_requested()` for the new
      wrapper type, same as the markdown wrapper is handled.
- [x] File explorer (`file_explorer_widget.py`): images get their own `image`
      item type (openable, 🖼️ icon) instead of the greyed-out unopenable
      `binary` type; double-click and context-menu "Open" route to the viewer.

## 4. Output embedding: tools/model embed a repo image in chat output

This becomes a **permanent** part of conversation history, replayed into
every future request. It is independent of `vision_enabled` for the
user-facing half (rendering for the human always works), but the
model-facing half (what gets replayed to the LLM) is exactly the kind of
vision content that flag governs.

- [ ] Model/tool writes normal markdown `![alt](path.png)` referring to a
      repo file, exactly as it would reference any other file by path.
- [ ] At **message-finalization time** (when the assistant turn is finalized
      in `live_session.py`, not at render time), scan the message text for
      image references that resolve to an existing VFS file. For each one
      found:
      - [ ] Read full-quality bytes, hash them (sha256).
      - [ ] Write `.forge/images/<sha256>.<ext>` (full quality, original
            format) via a normal VFS binary write — this is what the user's
            rendered chat displays, and it commits/versions like any file.
      - [ ] Use Pillow to produce a downscaled/recompressed
            `.forge/images/<sha256>.low.jpg` — this is the copy that gets
            embedded as base64 into the `IMAGE_CONTENT` block that's actually
            stored in session history and replayed to the model on every
            future request. Keeping this one small is the whole point: it's
            paid for on every single subsequent request, forever.
      - [ ] Rewrite the markdown reference in place to point at
            `.forge/images/<sha256>.<ext>` (the full-quality one) instead of
            the original path, so the user-facing rendering survives the
            source file later being edited or deleted.
  - [ ] If the path doesn't resolve to an existing image file, leave the
        markdown text untouched (no fallback guessing).
- [ ] `render_markdown()` in `tool_rendering.py`: resolves `.forge/images/...`
      paths to actual bytes for the `<img>` tag the user sees (always the
      full-quality file, never the `.low.jpg`).
- [ ] Add `Pillow` to project dependencies (`pyproject.toml`) — only needed
      for this section.

## Phasing

1. Settings toggle + VFS binary read/write + `ToolContext` bytes methods.
2. Context mechanism (§1): `IMAGE_CONTENT` block, multimodal `to_messages()`,
   `update_context` image support — full quality, live.
3. Tab viewer (§3).
4. Output embedding (§4): Pillow dependency, `.forge/images/` dual-storage,
   markdown rewrite at finalization, compaction interaction (§2).

## Resolved decisions

- **`.forge/images/` cleanup**: no GC needed. It's cleaned up by "clear
  session" like the rest of session-local state, and the files remain
  recoverable from git history regardless.
- **Pillow thumbnail params**: max 512px longest side, JPEG quality 70.
- **`vision_enabled=False` + image `update_context`**: hard tool error (not a
  silent skip). Matches "No fallbacks".
- **No per-request image size/count guard.** Images aren't unusually large
  compared to the chonkiest text files we already allow into context; no
  special-casing needed.
