# Forge

An AI-assisted development environment that treats git as the source of truth.

## Features

- Git-backed operations - all AI changes are commits
- Tool-based AI interactions - LLM can only use approved tools
- Multiple concurrent AI sessions on separate branches
- Beautiful markdown/LaTeX rendering for AI conversations
- Safe, opt-in AI assistance

## Installation

```bash
pip install -r requirements.txt
```

## Usage

```bash
python main.py
```

## Architecture

- Each AI session runs on its own git branch
- Tools are defined in `./tools/` directory
- All AI modifications go through git commits
- Filesystem is a view of git state, not the source of truth
