# Glossary

- **Turn**: Everything from the last user message to the stop token. A turn may include many tool calls.
- **Step**: A single [input, AI response] pair. Either [user message, AI response] or [tool results, AI response].

# No fallbacks

No fallbacks! No try/except, no fallback codepaths, no "kept for compatibility.
We do *not* have the space in the context to do this whole project and also hedge.
There Is Only One Way To Do It, if that fails, we just fail.
Errors and backtraces are holy.

Try/except will be the *very last* paths added to the codebase.

# Naming conventions

- **Don't alias things with different names** - If you import/re-export something, keep the same name. Different names in different places make the codebase harder to navigate and break tooling (summaries, grep, etc.). If you find an existing alias with a different name, mark it with `# FIXME: confusing alias` for later cleanup.

# When uncertain, add prints

When you're uncertain about what's happening in the code, don't guess.
Add print statements to see what's actually going on.
Print variable values, execution flow, state - whatever you need to understand the issue.
Once you have the data, you can fix it properly.

Guessing wastes time and context. Prints give you certainty.
