# Glossary

- **Turn**: Everything from the last user message to the stop token. A turn may include many tool calls.
- **Step**: A single [input, AI response] pair. Either [user message, AI response] or [tool results, AI response].

# Never touch the working directory

Git operations must NEVER operate on the working directory or `repo.index`.
All file operations go through the VFS, and all git operations use in-memory trees.

The working directory is the user's space. We only touch it to fast-forward after commits
when the user's branch is checked out (so they see our changes).

If you see `repo.merge()`, `repo.checkout()`, `repo.index.add()`, or similar - that's a bug.
Use `merge_trees()`, `create_blob()`, `create_commit()` with in-memory tree building instead.

# No fallbacks

No fallbacks! No try/except, no fallback codepaths, no "kept for compatibility.
We do *not* have the space in the context to do this whole project and also hedge.
There Is Only One Way To Do It, if that fails, we just fail.
Errors and backtraces are holy.

Try/except will be the *very last* paths added to the codebase.

# Naming conventions

- **Don't alias things with different names** - If you import/re-export something, keep the same name. Different names in different places make the codebase harder to navigate and break tooling (summaries, grep, etc.). If you find an existing alias with a different name, mark it with `# FIXME: confusing alias` for later cleanup.

# When compacting, note what's DONE

When using compact() to summarize previous tool calls, clearly mark what work was completed.
Use past tense and be explicit: "Fixed X by changing Y" not "Need to fix X".

The compacted summary is often all I have to know what happened. If edits were made,
say which files were edited and what changed. If something was created, say what.

# When uncertain, add prints

When you're uncertain about what's happening in the code, don't guess.
Add print statements to see what's actually going on.
Print variable values, execution flow, state - whatever you need to understand the issue.
Once you have the data, you can fix it properly.

Guessing wastes time and context. Prints give you certainty.

# Ownership and "need to know"

When you need data, ask: **"Who should I be asking, and why do they know?"**

If you find yourself reaching through an object to get something (`obj.manager._repo`),
that's a sign of confused ownership. Either:
1. You should have been given that thing at construction time (dependency injection)
2. The object you're asking should answer the question for you (encapsulation)
3. You're asking the wrong object entirely

**Prefer construction over accessors.** If `AIChatWidget` needs the repo, pass it in
`__init__`, don't reach through `session_manager._repo`. If you weren't given it,
maybe you're not supposed to have it.

**Accessors should answer questions, not hand out objects.** Instead of:
```python
repo = session_manager._repo  # Bad: "give me your repo"
if repo.is_workdir_clean(): ...
```

Do:
```python
if session_manager.is_workdir_clean(): ...  # Good: "is the workdir clean?"
```

The session manager *owns* the repo, so it can answer questions about it.
We don't need to know the repo exists. This keeps coupling low and makes
each file understandable on its own.
