# Validation — 0.1.0

Validated on 22 October 2026 (America/Toronto), macOS, Cinema 4D **2024.2** (`2024200`), embedded Python **3.11.4**. The installed official Codex CLI reported `0.163.0-alpha.11.1`.

## Automated checks

**58 tests passed.** Coverage includes task ordering, steering, Stop/Resume, error recovery, uncertainty handling, interrupted-task admission, chat history, model response parsing, reasoning options, local-only transport, pending approval replay, installer backups and installing an extracted release bundle independently of the source checkout.

## Cinema 4D native checks

The installed Cinema API created a separate test scene containing a cube, a material and texture tag, a camera and a light. It inserted two animation keys, saved recovery copies and a new `.c4d`, rendered a real 320×240 PNG with Standard, and reopened the saved file to verify its objects, material and keys.

The final fingerprint checks passed both immediately after editing and after deferred editor updates/rendering. An idle scene retained its revision; a real transform change invalidated it. The fingerprint ignores `ID_ICONCHOOSER_SETTINGS`, which Cinema lazily adds for its editor icons. These tests run in Cinema, not against an API mock.

Evidence: `tests/c4d-native-result.json`.

## Real Codex workflow

The plugin discovered seven available models with provider-supplied thinking levels. With **GPT-6.1-Sol**, it created a sphere, preserved the existing scene, assigned an orange material, and returned measured scene results. A separate clean workflow verified Manual approval before rendering, switching to Auto, sequentially rendering a PNG and saving a `.c4d` from queued messages. Both files were checked on disk.

Deliverable evidence: `examples/Codex-Demo.c4d` and `examples/Codex-Demo.png`. Raw workflow evidence is in `tests/live-workflow-result.json` and the local Cinema chat store. The combined exploratory workflow report is not marked wholly successful: its later, longer local-model task was stopped after slow API repairs.

## Real LM Studio workflow

The local server listed its available models and supported thinking options. The already loaded **qwen3.8-27b-uncensored-mlx** generated a Python tool call that created a new cube in the actual Cinema document. The successful tool result was received; the object count increased by exactly one, its unique name and Cube type matched, and its measured dimensions were **200×200×200**. A new `.c4d` copy was saved. The test stopped the model's follow-up generation after this execution proof.

The single-step check took **42.3 seconds**. This validates a real local-model scene step; it is not a claim that this model can reliably complete arbitrary modeling requests. During a previous natural-language attempt it invented an API constant and needed repairs. The system instructions now explain the verified transform and lookup APIs.

Evidence: `tests/local-step-result.json` and `examples/LM-Studio-Demo.c4d`.

## Native panel and final installed state

The installed command opens the native chat panel. After native panel reinitialization, provider, model and Manual mode remained selected and the companion replayed chat/state. Pending approval replay is also covered by the socket protocol test. The panel was left open with Codex, GPT-6.1-Sol, Low thinking, Manual apply, a new chat and an empty queue.

Evidence: `tests/panel-state-result.json` and the visible Cinema window.

## Fixed during validation; remaining limits

A freeze during repeated development-window reloads was traced to native dialog disposal during Python garbage collection. The dialog-to-bridge reference cycle was removed with a weak callback; subsequent clean native/provider workflows remained responsive. Long-session and repeated real workspace-docking soak tests are still needed.

Only the built-in Standard still renderer was verified. Redshift, third-party renderers, animation batch rendering, simulation orchestration, other Cinema versions and Windows remain unvalidated. Image attachment collection and transport are implemented; end-to-end vision modeling is not part of this validation. Third-party custom parameter data and external asset changes are not completely represented by the scene fingerprint. Generated Python is not sandboxed.

The plugin uses development command ID `1069917`; obtain a unique Maxon ID before public distribution. This release is a local alpha.

## 0.1.1: typing while another task is running

The input failure was reproduced on the installed native panel in Cinema 4D 2024.2. `SetString(HISTORY, ...)` preserved composer focus; `SetMultiLinePos(HISTORY, ...)` transferred keyboard focus to the read-only history. Streaming updates repeatedly performed the latter, preventing the user from composing the next queued task.

The history now only repaints when its visible text changes, and skips the focus-changing scroll while the composer is active. Sending or steering a task returns focus to the composer. In the real host, 40 visible and 40 hidden stream updates preserved composer focus and draft text; a busy-state update also left the composer focused. The original draft was restored after the check. The updated methods were applied to the existing panel without recreating native windows or interrupting the companion.

All 63 automated checks passed, including five focus regression checks and the existing 30-message FIFO queue test. Native evidence: `tests/input-focus-before-result.json`, `tests/input-focus-detail-result.json`, `tests/input-focus-result.json`. Re-run the native check with `tests/c4d_input_focus.py` through `runpy`. This focused check does not constitute a long-session keyboard/IME soak test.

## 0.1.2: long conversations

The reported failure was an LLMLINK guard at 160,000 prompt characters, rather than a 10-message model quota. The affected archive contained 12 user requests and 81 intermediate records, about 2.15 million serialized characters. The new projection reduced its planning data to 60,110 characters without altering the archive. The old hard-stop message was removed.

All 75 automated checks passed. A 1,000-request fixture exercised automatic summary batches, exact preservation of the latest request/repair code and measured scene identity, cache reuse after reopening, incremental updates, cancellation, corrupt cache rejection and bounded archive recall. A valid archive over 64 MiB reopens successfully. Memory responses proposing Python are rejected without execution.

A real GPT-6.1-Sol check used a copy of the affected conversation, forced a smaller 40,000-character context budget and summarized 79 earlier records into 9,052 characters of memory. Codex then described the existing scene and explicitly distinguished unfinished lighting/rendering. The check took 92.5 seconds, executed zero scene actions, and left the source history unchanged. Evidence: `tests/context-live-result.json`. The 1,000-request test is an automated fixture, not 1,000 real API calls.

Models still have finite context windows, provider usage limits still apply, and semantic summaries can omit details. The full archive and bounded recall are retained to recover earlier specifics. This release removes the plugin's early conversation stop; it does not claim physically infinite model memory or an unlimited provider subscription.

The installed 0.1.2 companion was restarted only after native checks confirmed no active task, render or queued work. The existing native dialog and Cinema document were retained. Authentication, the same chat ID and history replay were verified, and the composer retained focus. Installed sources matched the project byte-for-byte. Evidence: `tests/context-panel-result.json`. Historical context-full errors remain visible as past events; the new provider no longer emits that hard-stop error.

## 0.2.0 reliability audit — 2026-10-22

- 99 automated tests passed in 6.166 seconds on the final audit source. Native-only tests are separate, not counted in that number.
- Native release harness passed 10 checks in Cinema 4D 2024.2: policy rejection, stale document rejection, SystemExit containment, Python timeout, modeling/material/animation/checkpoint, PNG render, saved scene reopen, attached-only asset import, settled scene fingerprint, preservation of the original user scene.
- Real Codex and already-loaded LM Studio returned valid read-only answers. A real inherited MCP sentinel was disabled and did not start.
- Context projection benchmark on the affected real archive: 0.16342s / 13.35 MB peak before; 0.02026s / 0.67 MB peak after, both 60,110 characters. Ten iterations with tracemalloc; generation latency is separate.
- `tests/c4d_release_audit.py` is the reproducible native harness. Its first run exposed a test-driver completion bug, corrected before the final passing run. Import measurements wait for Cinema's normal animation update before checking idle stability.
- Full findings and remaining release gates: `docs/RELEASE_AUDIT.md`.

Installed 0.2.0 activation also passed five native checks: original-chat reconnect, 40 history updates retaining composer focus/text, reconnection while the failed process was still alive, draft/provider preservation across that reconnect, and unchanged original document with its original draft and empty queue. The existing docked dialog was retained; Cinema was not restarted. Source/plugin Python files were compared byte for byte.
