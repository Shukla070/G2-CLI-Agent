# Setup

## Prerequisites

- **Python 3.11+** (tested on 3.12)
- **An Anthropic API key** with access to Claude Sonnet

## Installation

1. **Clone the repository** and `cd` into it:

```bash
git clone <repository-url>
cd lantern
```

2. **Install the package** (editable mode, with dev dependencies):

```bash
python -m pip install -e ".[dev]"
```

This installs all runtime dependencies (`anthropic`, `typer`, `rich`, `pydantic`, `python-docx`, `pypdf`, `python-dotenv`) and test dependencies (`pytest`, `pytest-mock`).

## Configure the API Key

Copy the template and fill in your key:

```bash
cp .env.example .env
```

Then edit `.env` in the **repository root** (not the workspace):

```env
ANTHROPIC_API_KEY=sk-ant-...your-key-here...
```

Lantern loads this via `python-dotenv` at startup. The `.env` file is gitignored and must never be committed.

> **Note:** A `.env.example` file is included as a template.

## Running Lantern

### Interactive Mode (conversation loop)

```bash
cd example_workspace
python -m lantern.cli.main --interactive
```

This starts a REPL where you can chat with Lantern in natural English. The agent will search documents, read files, and answer questions — routing every action through the safety stack.

### With a specific workspace

```bash
python -m lantern.cli.main --workspace /path/to/your/workspace --interactive
```

### One-shot prompt

```bash
python -m lantern.cli.main --workspace example_workspace --prompt "What files are in the workspace?"
```

### Session management

```bash
# Start a fresh session
python -m lantern.cli.main --interactive --new

# Resume a previous session
python -m lantern.cli.main --interactive --resume session-abc123def456
```

### CLI flags

| Flag | Description |
|---|---|
| `--workspace`, `-w` | Workspace directory (default: current directory) |
| `--interactive` | Run the conversation loop |
| `--new` | Start a fresh session (skip resume) |
| `--resume <id>` | Resume a specific session by ID |
| `--prompt <text>` | Run a single prompt and exit |
| `--help` | Show all options |

## Running Tests

```bash
# Full suite
pytest -q

# Verbose with test names
pytest -v

# Specific test file
pytest tests/unit/test_sandbox.py -v

# Just the Phase 3 tests
pytest tests/unit/test_extraction.py tests/unit/test_read_tools.py tests/unit/test_policy_loading.py -v
```

Expected output:
```
276 passed, 8 skipped
```

The 8 skipped tests are symlink-escape tests that require admin privileges on Windows. They pass on Linux/macOS.

## Example Workspace

The `example_workspace/` directory is the demo workspace used for all transcripts and testing. It contains:

- **5+ research folders** with source documents (~300+ words each)
- A combined policy document at `policies/embargo_policy.md` covering both embargo handling and citation requirements
- **An output folder** for generated content
- **A prompt-injection test file** (`corrupted_note.txt`) containing a fake "SYSTEM OVERRIDE" instruction to verify the defense works

## Transcript Artifacts

The `transcripts/` directory contains 6 recorded API transcripts demonstrating:

1. `01_ordinary_search_question.md` — basic document search and question answering
2. `02_search_and_generate.md` — search + synthesis into a new document
3. `03_ambiguous_choice.md` — multiple similar files, MEDIUM confidence options
4. `04_consequential_approval.md` — delete requiring HIGH approval
5. `05_session_resume.md` — session paused and resumed from disk
6. `06_adversarial_escape.md` — sandbox escape and prompt-injection attacks

### Regenerating transcripts

Transcripts are not hand-written. `scripts/record_transcript.py` wraps the real `AnthropicClient` with a logging proxy, drives the actual `Orchestrator` against `example_workspace`, and formats the raw request/response traffic into Markdown — so every tool call, tool result, and confidence level in a transcript file is exactly what the API and the tools actually did. Requires a real `ANTHROPIC_API_KEY` in `.env`; each run makes billed API calls.

```bash
python scripts/record_transcript.py \
    --workspace example_workspace \
    --out transcripts/01_ordinary_search_question.md \
    --title "Ordinary Search / Question" \
    --new \
    --turn "Which files mention coastal erosion, and what do they say?"
```

For multi-turn or stop-and-resume scenarios, see the usage examples in the script's module docstring.

## Troubleshooting

| Problem | Solution |
|---|---|
| `ModuleNotFoundError: lantern` | Run `pip install -e .` or set `PYTHONPATH=src` |
| `ANTHROPIC_API_KEY not set` | Create `.env` file with your key |
| `anthropic package not installed` | Run `pip install anthropic` |
| Symlink tests failing on Windows | Expected — they require admin privileges |
