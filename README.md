# Forge

A GUI AI coding assistant where git is the source of truth.

## What is this?

Forge is a desktop IDE that lets you collaborate with AI on code. Every AI change is a git commit on a branch, so you can review, diff, revert, or merge just like any other code. No magic, no hidden state - just git.

## Features

- **Git-native**: AI works on branches, every change is a commit
- **Multi-session**: Work on multiple branches simultaneously in tabs  
- **Tool-based**: AI uses explicit tools (search/replace, write file, etc.) - no diff application failures
- **Working directory protection**: Warns before overwriting uncommitted local changes
- **Cost tracking**: See what you're spending on API calls
- **Model picker**: Choose from any OpenRouter model

## Installation

```bash
pip install -e .
```

You'll need an [OpenRouter](https://openrouter.ai/) API key. Set it in Settings (gear icon) or via `OPENROUTER_API_KEY` environment variable.

## Usage

```bash
forge
```

Or run directly:

```bash
python main.py
```

## How it works

1. Open a branch (or create a new one)
2. Chat with the AI about what you want to build
3. AI makes changes via tool calls, each becoming a git commit
4. Review changes in the diff view, merge when ready

The AI sees:
- Repository file summaries (generated at session start)
- Files you explicitly open (full content)
- Conversation history

## Architecture

- **VFS (Virtual File System)**: AI reads/writes through a git-backed abstraction
- **Session per branch**: Each branch tab is an independent AI session
- **Tool approval**: Custom tools in `./tools/` require explicit approval before use
- **Prompt caching**: File order is optimized so unchanged files stay cached

## License

GPL-3.0 - see LICENSE file.
