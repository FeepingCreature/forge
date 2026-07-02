# Image Support TODO

Goal: let the model see repo images, view them in tabs, and let tools/model
embed repo images in output for the *user*. All model-facing behavior is
gated by a settings toggle for models without vision support.

## Key simplification: images are ALWAYS low-detail to the model

No recency-based degradation tiers. Every image the model sees is sent at a
fixed `detail: "low"` (~85-96 tokens, OpenAI/OpenRouter vision API). There is
no `"auto"`/`"high"` tier at all.

Rationale: the model can't usefully inspect fine detail anyway, and any real
work happens against the full-quality file in the VFS directly (edits, tools,
etc. never go through the vision snapshot). If the model genuinely needs a
closer look... it can't get one — there is no higher tier. That's fine: images
are for orientation ("what does this chart/screenshot roughly show"), not
pixel inspection.

This also means: **no degradation-over-time logic, no "is this the most
recent image" bookkeeping.** Compaction (§2) is the only way an image leaves
history. Pillow becomes a real dependency, used to produce the small
low-detail thumbnail once at attach-time (capped resolution + JPEG
recompression) — kept small because it's replayed in session history forever
as base64.

## The model only ever sees an image by having it in context

There is exactly **one** way an image becomes visible to the model: it is in
`active_files` (context), same as any other file. No auto-attachment, no
implicit "the model just wrote this so it can see it" magic.

Consequence: **a tool that generates an image and wants the model to be aware
of it must explicitly add it to context itself** (same call a human would make
via the file explorer). This keeps the mental model uniform with every other
file type and avoids a second hidden trigger path.

Embedding an image in chat output for the *user* (§4) is a **completely
separate, unrelated concern** — it doesn't touch the model's context at all,
doesn't require `vision_enabled`, and doesn't imply the model can see the
image it just embedded (if it wants that, it must `update_context` it too).

## Settings

- [ ] Add `llm.vision_enabled` to `Settings.DEFAULT_SETTINGS` (default `False`).
- [ ] Expose toggle in `settings_dialog.py`.
- [ ] Single gate point: `update_context`/`add_active_file`, when the file is
      an image — if `vision_enabled` is off, adding an image file to context
      should fail/refuse clearly (no silent no-op) rather than pretend to
      succeed. Tab viewing (§3) and output embedding (§4) are unaffected by
      this flag — that's for the human, not the model.

## 1. Model can see repo images (core)

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
      full-quality bytes, downscale/recompress via Pillow into a small
      low-detail thumbnail, base64-encode *that*, and store it as the
      session-visible snapshot (see below). If `vision_enabled` is off,
      refuse with a clear error.
- [ ] Prompt manager (`forge/prompts/manager.py`):
  - [ ] New `BlockType.IMAGE_CONTENT` whose `content` *is* the base64
        thumbnail data URL itself (self-contained — no filepath lookup
        needed to replay it into a future prompt).
  - [ ] `append_image_content()` — mirrors `append_file_content()`'s
        tombstone-and-move-to-end semantics: re-adding the same file to
        context replaces the old snapshot the same way editing a text file
        moves it to the end of the prompt stream.
  - [ ] `to_messages()`: needs to emit multimodal content
        (`content: [{"type": "text", ...}, {"type": "image_url", "image_url":
        {"url": ..., "detail": "low"}}]`) instead of a plain string, for any
        message containing an image block. Check cache_control placement
        still makes sense for these turns.
  - [ ] Token estimation (`_estimate_tokens` is char-based) doesn't apply to
        images — use a fixed constant (~90 tokens) per image block for mood
        bar / budget purposes, matching the real low-detail API cost.
- [ ] `LLMClient`: no changes expected — payload already passes `messages`
      through untouched, so multimodal content blocks should pass through
      fine as long as `PromptManager` builds them correctly. Verify OpenRouter
      accepts the standard OpenAI vision shape for the configured model.
- [ ] Add `Pillow` to project dependencies (`pyproject.toml`).

## 2. Compaction drops images

- [ ] When `compact_messages(from_id, to_id, ...)` tombstones a range, any
      `IMAGE_CONTENT` block in that range is dropped outright — same as any
      other compacted content, no special-casing needed since there's only
      one detail tier.
- [ ] Removing an image from context (`remove_active_file`) should behave
      exactly like removing a text file — tombstone it via
      `remove_file_content`-equivalent for images.

## 3. View an image in a tab

Always full quality — unrelated to the model-facing low-detail thumbnail.

- [ ] `BranchWorkspace`: add `get_file_bytes(filepath)` so UI doesn't reach
      into `vfs.base_vfs` directly (ownership rule — ask the owner).
- [ ] New `ImageViewerWidget` (QLabel/QPixmap, minimal zoom/fit-to-window).
- [ ] `branch_tab_widget.py` `open_file()`: extend the existing `.md` →
      `MarkdownPreviewWidget` dispatch pattern with an image-extension branch
      that creates `ImageViewerWidget` instead of `EditorWidget` (no text
      editor needed for binary images).
- [ ] Update `_find_tab_index()` and `_on_tab_close_requested()` for the new
      wrapper type, same as the markdown wrapper is handled.

## 4. Tools/model embed repo images in chat output (for the user)

This is purely a rendering concern for the human reading the chat. It is
**independent of `vision_enabled`** and does **not** put anything in the
model's context — if the model wants to see the image it embedded, it must
also `update_context` it (§1), same as any other file.

- [ ] Model/tool writes normal markdown `![alt](path.png)` referring to a
      repo file, exactly as it would reference any other file by path.
- [ ] At **message-finalization time** (when the assistant turn is finalized
      in `live_session.py`, not at render time), scan the message text for
      image references that resolve to an existing VFS file. For each one
      found:
      - [ ] Read full-quality bytes, hash them (sha256).
      - [ ] Write them to a stable, content-addressed path under
            `.forge/images/<sha256>.<ext>` via a normal VFS binary write (this
            commits/versions like any other file — no separate blob store).
      - [ ] Rewrite the markdown reference in place to point at
            `.forge/images/<sha256>.<ext>` instead of the original path. This
            makes the rendered chat immune to the original file later being
            edited or deleted — the `.forge/images/` copy is the permanent
            record for that message.
  - [ ] If the path doesn't resolve to an existing image file, leave the
        markdown text untouched (no fallback guessing).
- [ ] `render_markdown()` in `tool_rendering.py`: needs no new logic beyond
      resolving `.forge/images/...` paths to actual bytes for the `<img>` tag
      (e.g. a `file://`-style or data-URL rendering of the VFS path at render
      time — check how the chat view currently resolves any relative URLs, if
      at all).

## Phasing

1. Settings toggle + VFS binary read/write + `ToolContext` bytes methods.
2. Core vision: Pillow thumbnailing, `IMAGE_CONTENT` block, multimodal
   `to_messages()`, `update_context` image support.
3. Compaction drop for image blocks.
4. Tab viewer.
5. Chat-output embedding (`.forge/images/` content-addressed store + markdown
   rewrite at finalization + render-time resolution).

## Open questions

- `.forge/images/` will accumulate forever (content-addressed, never
  cleaned up) since every embedded image creates a permanent file. Do we want
  any GC story, or is "images are cheap and rare" an acceptable answer for now?
- Pillow thumbnail parameters (max dimension, JPEG quality) — need a concrete
  starting point, e.g. max 512px longest side, quality 50.
- Should refusing `update_context` on an image when `vision_enabled=False`
  surface as a tool error to the model, or should the image simply be silently
  excluded from context stats/prompt (still shown in file explorer)? Leaning
  towards explicit error — matches "No fallbacks" principle.
