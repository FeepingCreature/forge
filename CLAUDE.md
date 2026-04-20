# Editing files that mention edit-block syntax

When a search or replace body contains literal `</edit>`, `</search>`, or
`</replace>` (e.g. when editing this file, the system prompt, or test fixtures),
use the **nonced** form: pick any short token and append `_TOKEN` to the
`edit`, `search`, and `replace` tag names. Example:

    <edit_x9 file="docs.md">
    <search_x9>
    Close with </edit>.
    </search_x9>
    <replace_x9>
    Close with </edit_NONCE> (any matching suffix).
    </replace_x9>
    </edit_x9>

If a block fails to parse, you'll get an explicit error rather than a silent drop.

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

# When you need external information you can't get, stop and ask

If you need to check external documentation (API docs, library docs, etc.) and
there's no tool available to do that — **stop and tell the user**. Don't try to
reverse-engineer the answer by grepping the codebase or making speculative
scout calls. That wastes context and time without producing certainty.

Say: "I need to check [X docs] but I don't have a web search tool. Can you
look up [specific question] or paste the relevant docs?"

# When uncertain, add prints

When you're uncertain about what's happening in the code, don't guess.
Add print statements to see what's actually going on.
Print variable values, execution flow, state - whatever you need to understand the issue.
Once you have the data, you can fix it properly.

Guessing wastes time and context. Prints give you certainty.

# Ownership and "need to know"

When you need data, ask: **"Who should I be asking, and why do they know?"**

Think of it as roleplay - each object has a role and knows certain things:
- "Is it safe to proceed?" → Ask the session manager (it knows about workdir state)
- "What files are in context?" → Ask the session manager (it tracks that)
- "Render this message" → That's your job, chat widget

If you find yourself asking "hand me that object so I can ask it something", 
stop. Ask the owner to answer the question instead. They know their internals,
you don't need to.

**If you weren't given it at construction, you probably don't need it.**
Needing to reach through objects (`obj.manager._repo`) suggests confused ownership.
Either you should have been given it in `__init__`, or you should be asking
a different question.

**Accessors answer questions, they don't hand out objects.**
```python
# Bad: "give me your repo so I can check something"
repo = session_manager._repo
if repo.is_workdir_clean(): ...

# Good: "is the workdir clean?"
if session_manager.is_workdir_clean(): ...
```

The session manager owns the repo, so it answers questions about repo state.
The chat widget doesn't even need to know repos exist.
