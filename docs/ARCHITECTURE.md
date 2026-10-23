# LLMLINK Cinema 4D internals

`plugin/LLMLINK/LLMLINK.pyp` registers a native `CommandData` and shuts down the companion and managed render worker on application exit. `llmlink_c4d/dialog.py` provides the dockable `GeDialog`; Cinema's normal native controls handle editing, selection and paste. A weak callback reference avoids a cycle between the dialog and its bridge.

`bridge.py` starts an external Python companion with the selected interpreter. It passes a fresh session secret through stdin, authenticates a private loopback socket, and pumps bounded, length-prefixed JSON messages from the main-thread dialog timer. No HTTP or browser server is exposed. Provider requests do not run on Cinema's main thread.

`companion.py` owns conversations, task admission, FIFO scheduling, approval state and provider retries. Each queued message retains its provider configuration. Apply mode remains a live session setting. A stopped or uncertain operation pauses subsequent execution. Pending messages persist; an interrupted active task is reported for review and is not automatically replayed. The busy flag is set before the next queued item is removed, preventing an observable idle gap between jobs.

`providers.py` adapts the existing LLMLINK Blender clients. Codex uses the official App Server via stdio, live model discovery and JSON output schemas. LM Studio uses local model discovery and streaming OpenAI-compatible tool calls. Provider responses are validated before code is submitted to Cinema. Shell commands and external MCP calls are disabled in the Codex requests.

`scene_tools.py` reads the current document and applies Python only on the main thread. A scene fingerprint includes editable object/tag parameters, transforms, polygon geometry, material/shader parameters, animation key values, render settings, scene camera and object selection. Deferred editor dirty counters are excluded so cache updates do not invalidate unchanged scene contents. Third-party custom parameter data is opaque; the fingerprint is not a complete change detector for arbitrary plugin-specific data or external asset changes.

Each Python step verifies document identity and the expected fingerprint, saves a recovery copy of a detached clone, starts a Cinema undo group, and executes a bounded Python step with `c4d`, `doc`, `math` and `llmlink`. Trace-based limits interrupt ordinary Python loops but cannot preempt a blocking native API call. This is not a security sandbox.

`llmlink.render()` records a render request and returns from generated Python. A managed `C4DThread` renders a detached document clone. Its result returns to the main-thread timer only when the worker is done; PNG saving must succeed before the companion receives a successful tool result. The worker has a 30-minute cooperative limit and uses Cinema's native break mechanism. Stop cannot undo prior scene or file changes.

`chat_store.py` keeps atomic chat snapshots; `attachments.py` copies explicitly selected images and validates their hashes. No project directory is scanned for attachments. Configuration, chats, queues, checkpoints and output live outside the plugin installation.

The package builder excludes runtime interpreter settings, history, logs, caches and test outputs. The installer stages the new plugin and moves previous versions outside the scanned plugins directory so Cinema cannot load duplicate command IDs.

## Conversation context and archive recall

`context_window.py` builds a read-only planning projection. It keeps one current scene, bounds diagnostic output and historical attachment excerpts, and omits earlier code while retaining the latest repair code. Above the planning budget it summarizes the earlier prefix in bounded batches with the selected provider. The summary is validated as data-only `done` with empty code; it never reaches the scene executor. Private cache files atomically store a prefix fingerprint and summary and are reused only when the original history prefix matches. All raw history remains in ChatStore and the append-only event log. `recall` searches that history and returns bounded excerpts without invoking Cinema Python. Recent input, scene identity/revision and active repair code are pinned separately from older memory.

## Reliability boundaries in 0.2.0

- `persistence.py` publishes private JSON snapshots by temporary file, file flush/fsync and atomic replace. Draft sidecars avoid full-archive writes on every keystroke. A sudden power loss is not covered by a filesystem transaction across multiple files.
- The complete active task is recorded before queue removal. Restart excludes its ID even if it remains in the queue snapshot. New work is bound to the active task's measured document; an idle request may intentionally target a new document. Unknown outcomes remain paused.
- Message configuration is validated as part of admission, so a rejected provider change cannot silently send the task to the old provider.
- `code_policy.py` rejects unsafe syntax paths before approval and again immediately before host execution. It is defense in depth, not a Python/native API sandbox. `Helpers.import_asset` only accepts an explicitly attached path supplied by the authenticated companion.
- `process_io.py` uses bounded event queues, bounded lines and bounded stderr. Process cancellation releases blocked readers. Codex MCP servers are individually disabled through leaf config overrides, without copying redacted configuration values.
- Failed bridges release their socket, clear authentication and allow reconnect even if the old process is still alive. A pending render must finish cancellation before replacement. Late render results cannot enter a new session.
- Python `BaseException` results are contained at the execution boundary; missing scene measurements cannot strand a completed render worker. Native crashes and blocking C++ calls remain outside Python's control.
