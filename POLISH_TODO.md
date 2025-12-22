# Forge Polish TODO

Small improvements and friction points from dogfooding.
These are real issues but not strategic priorities.

---

## CLI & Startup

- [ ] `forge <filename>` should open that file on startup
- [ ] Restore open files on start from XDG cache (`~/.cache/forge/open_files.json` keyed by repo)
- [ ] JS files for webview should be bundled in app (no HTTP requests on startup)

---

## File Explorer

- [ ] Warning icon (⚠️ large file) should bubble up to parent directories
- [ ] File mouseover tooltip: show size, maybe summary description
- [ ] Global search results shown as icons/markers in explorer view
- [ ] Explorer could become generic "tool view" with tabs (files, search results, etc.)

---

## Search

- [ ] Global search hotkey (Ctrl+Shift+F)
- [ ] Search in webview (chat history)
- [ ] Ctrl+Return in search to ask model "find code that does X" (AI-assisted search)

---

## Editor & UI

- [ ] Make UI panels arrangeable/dockable
- [ ] Hotkey configuration system
- [ ] Configurable syntax highlighting/theming (how deep?)
- [ ] Performance audit at some point

---

## Code Completion

- [ ] Small-model completion (Haiku is very cheap)
- [ ] Send current file + cursor position, ask "what goes here"
- [ ] OpenRouter doesn't have dedicated completion API, but chat works

---

## Branch & Session Management

- [ ] Branch fork dialog: option to fork with or without session history
- [ ] "Reset conversation to here" in chat history (rewind to previous point)
- [ ] Git graph: show dangling/recent commits not on any branch (reflog-based?)

---

## AI Behavior

- [ ] More aggressive compaction hints (model happily runs at 60k context)
- [ ] Long-term: AI can kick off autonomous work on separate branches via tool call

---

## AI Turn Interaction

- [ ] Option to get system notification when AI turn completes
- [ ] Branch tab marker when waiting for user input
- [ ] Allow user to type while AI is running (queued for next turn)
- [ ] Cancel button during AI turn
  - If streaming text (not tool): cancel request, mark as canceled, inject user comment
  - If in tool execution: cancel, discard pending VFS changes
- [ ] Pause button (only if OpenRouter supports pause/resume streaming - probably not)
- [ ] User text entered during streaming gets added at next turn (or interrupts if mid-speech)

---

## Tool Rendering

- [ ] `compact` tool needs pretty rendering in chat
- [ ] User-defined tools need a hook for custom pretty rendering

---

## Model Integration

- [ ] Quick way to ask summary model repo questions ("how do we do X?")
- [ ] Could integrate with global search - AI-assisted code discovery

---

## Random Ideas

- [ ] "Ask about repo" command - uses summary model to answer architecture questions
- [ ] Search + AI hybrid: normal search, then "explain these results"
