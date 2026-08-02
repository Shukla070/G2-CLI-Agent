# Transcript 05 — Session Stop and Resume
# Scenario: A session is interrupted mid-approval, then resumed from a new process.

## Starting Workspace State
- `research_notes/coastal_erosion_study.txt` — ~2 KB, coastal erosion research
- `outputs/` — empty output directory
- `policies/embargo_policy.md` — citation and embargo policy

## Session 1: Start and Pause

### User Message
```
Write a summary of the coastal erosion research to outputs/erosion_summary.txt.
```

### Step 1: read_document
**Tool call:** `read_document(path="research_notes/coastal_erosion_study.txt")`
**Confidence:** NONE (read-only)

**Result:** (Full text of the coastal erosion study)

### Step 2: write_document
**Tool call:** `write_document(path="outputs/erosion_summary.txt", content="Coastal Erosion Summary\n\n...", confidence="NONE", rationale="Writing a new file to the output directory. Source is cited as required by citation policy.")`

**Action Gate evaluation:**
1. **Sandbox:** Path resolves inside workspace. ✅
2. **Classification:** `WRITE_NEW` (file doesn't exist yet) → floor = NONE.
3. **Confidence:** Model declared NONE, floor is NONE → effective = NONE.
4. **Outcome:** `EXECUTE` — write proceeds immediately.

**Result:** File created at `outputs/erosion_summary.txt`.

### Step 3: finalize_response
**Tool call:** `finalize_response(content="I've written the summary to outputs/erosion_summary.txt.", confidence="NONE", rationale="New file created with citation.")`

### Lantern's Response
```
I've written the summary to outputs/erosion_summary.txt, citing
research_notes/coastal_erosion_study.txt as the source material.
```

### User's Second Message (in same session)
```
Now overwrite that file with a version that includes the interview data too.
```

### Step 4: read_document
**Tool call:** `read_document(path="interviews/dr_thorne_interview.txt")`
**Confidence:** NONE

### Step 5: write_document (overwrite attempt)
**Tool call:** `write_document(path="outputs/erosion_summary.txt", content="Updated Coastal Erosion Summary\n\n...", confidence="HIGH", rationale="Overwriting an existing file. The user explicitly requested this, but the overwrite is destructive and requires approval.")`

**Action Gate evaluation:**
1. **Sandbox:** Path resolves inside workspace. ✅
2. **Classification:** `WRITE_OVERWRITE` (file already exists) → code-enforced floor = **HIGH**.
3. **Confidence:** Model declared HIGH, floor is HIGH → effective = HIGH.
4. **Outcome:** `AWAIT_INPUT` — turn paused, `PendingInteraction` saved.

### Lantern's Response (PAUSED)
```
[HIGH] Write to 'outputs/erosion_summary.txt' (this may overwrite an existing file).
Rationale: Overwriting an existing file. The user explicitly requested this,
but the overwrite is destructive and requires approval.
Reply 'yes' to approve or 'no' to cancel.
```

### Session State Saved
The session JSON is written atomically to `.lantern/sessions/session-abc123.json` with:
- Full turn history (user messages + assistant responses)
- `pending_interaction` containing the frozen overwrite decision

### ⚡ THE USER CLOSES THE APPLICATION ⚡
(Ctrl+C, process killed, or terminal closed)

---

## Session 2: Resume from New Process

The user launches Lantern again:
```bash
python -m lantern.cli.main --workspace example_workspace --interactive
```

### Automatic Session Restore
- `SessionManager.find_latest_session()` loads `session-abc123.json`.
- The session has a `pending_interaction` with `kind="tool_confirmation"`.
- The CLI detects this and **immediately displays** the pending approval:

```
Lantern — session session-abc123
Type 'exit' to quit.

⚠ Awaiting input
[HIGH] Write to 'outputs/erosion_summary.txt' (this may overwrite an existing file).
Rationale: Overwriting an existing file. The user explicitly requested this,
but the overwrite is destructive and requires approval.
Reply 'yes' to approve or 'no' to cancel.
```

### User Reply (from new process)
```
yes
```

### Resume Processing
1. `resume_turn("yes")` is called.
2. `_parse_confirmation("yes")` → `True`.
3. The sandbox **re-validates** `outputs/erosion_summary.txt` — the path is still valid.
4. The file is overwritten with the new content.
5. `pending_interaction` is cleared.
6. The model is re-invoked to complete the turn.

### Lantern's Response
```
Done. outputs/erosion_summary.txt has been updated with both the coastal
erosion study data and the interview findings from Dr. Thorne. Sources cited:
research_notes/coastal_erosion_study.txt, interviews/dr_thorne_interview.txt.
```

## Confidence Levels
- **Read operations:** NONE (no approval needed)
- **Initial write (new file):** NONE (new file in output directory)
- **Overwrite (existing file):** HIGH (code-enforced floor for overwrites)

## Key Resume Behaviors Demonstrated
1. **Session persistence survives process restart** — the JSON file contains all state.
2. **Pending approval is restored and displayed immediately** — the user sees exactly what they left off on.
3. **Path re-validation on resume** — the sandbox checks the path again, not trusting the frozen decision.
4. **No duplicate turns** — the conversation history is plain text, not raw API blocks, so replay works cleanly.
5. **Deterministic approval parsing** — "yes" is parsed by an allow-list, never by the model.

## Final Workspace Effect
**Created then overwritten:** `outputs/erosion_summary.txt` — final version includes both coastal erosion study and interview data, with source citations.
