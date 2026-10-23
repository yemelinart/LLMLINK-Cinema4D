# LLMLINK Cinema 4D

[Project website](https://yemelinart.github.io/LLMLINK-Cinema4D/) · [Downloads](https://github.com/yemelinart/LLMLINK-Cinema4D/releases)

AI chat inside Cinema 4D. Describe a scene change, review the generated Python, and apply it to the current document. Connect to **LM Studio** on your computer or to **Codex** through its signed-in official client.

**0.2.0 · hardened local build · macOS · Cinema 4D 2024.2.** Other Cinema versions and Windows have not been validated.

## Install

1. Save your scene. Run `Install.command` from the extracted installation bundle.
2. Restart Cinema 4D, then open **Extensions → LLMLINK**.
3. Select **Codex** or **LM Studio**, click **Connect**, choose a model and Thinking level.
4. Describe a task and click **Send / Queue**. In **Manual apply**, use **View code**, then **Apply change** or **Decline**.

The installer uses an existing Python 3.9+ interpreter and installs to the user plugins folder. It does not download Python, install packages, or require an administrator password. Previous plugin versions are backed up outside the scanned plugins directory. Chats and output files are stored separately from plugin code.

If automatic discovery cannot identify your Cinema installation:

```sh
python3 tools/install.py --plugins '/path/to/Cinema-user-preferences/plugins'
```

The preferences folder can be located from Cinema 4D Preferences. As an alternative, copy `plugin/LLMLINK` into its `plugins` folder. Obtain Python separately if your computer does not have Python 3.9 or newer. This build is not notarized; do not disable macOS security protections.

## Workflow

- Dockable native panel with resizable history and a native multiline composer.
- Provider model lists are discovered live. Thinking options come from each provider's model catalog; unsupported levels are not invented.
- **Manual apply** reviews each scene step. **Auto apply** executes subsequent proposed steps; modes can be changed while work is running.
- Messages submitted during a task wait in order. **Run next** promotes a queued message; **Steer** refines ongoing work after any committed scene operation finishes.
- **Stop** cancels future steps, cancels a running managed render, and pauses the queue. **Resume** continues queued work. Queued messages restored after reopening start paused.
- **+ File** attaches up to six references, including images for vision-capable models. Attachment names remain associated with their messages. Model vision support varies.
- **New chat / Open chat** switch between saved conversations after current and queued tasks finish. Drafts and attachment paths are retained.
- Every generated Python step saves a recovery `.c4d` copy before editing. The plugin checks that the active document has not changed since planning or approval.
- Objects, polygon modeling, materials, cameras, lights and animation are available through Cinema's Python API. Generated code can repair a failed step up to three times using measured partial scene results.

Renders use `llmlink.render()` on a detached scene clone in a Cinema worker thread. A completed step reports the PNG path only after the file has been saved. The default preview uses the built-in Standard renderer; `engine="current"` uses the scene renderer and requires any corresponding license. Redshift and third-party renderer workflows have not been validated. Still renders are implemented; animation batch rendering and simulation orchestration are not exposed as dedicated tools in this build.

Examples:

> Создай небольшой дом с двускатной крышей, окнами и дверью. Сохрани существующие объекты.
>
> Добавь камеру и свет, сделай рендер 640×480 и сохрани копию сцены.
>
> Добавь ключи анимации вращения объекта от 0 до 90 кадров.

## Providers

**Codex:** install and sign in to the official Codex client. LLMLINK uses its local App Server and lists models available to that account. No API key is copied or stored. Account limits apply. Built-in shell, file-patching and external MCP tools are disabled for the plugin's generated-step requests.

**LM Studio:** load a tool-capable model and start the local server, normally `http://127.0.0.1:1234/v1`. The plugin supports local HTTP endpoints without server authentication. It does not automatically download a model. If the model catalog exposes no thinking controls, only **Model default** is shown. Start with a small task and a low reasoning level where supported.

## Data and recovery

LLMLINK data lives in `LLMLINK` under Cinema's `C4D_PATH_PREFS` path. On the validated Mac this is the Cinema preferences folder's `prefs/LLMLINK` directory:

- `chats/` and `chat-*.jsonl`: private local history.
- `queue.json`: pending tasks and their provider settings.
- `attachments/`: selected image copies.
- `checkpoints/`: new `.c4d` recovery copies before edits.
- `output/`: saved scene copies and rendered PNGs.
- `companion.log`: connection diagnostics.

Open a checkpoint as a separate document to recover. Generated code is checked in both processes to reject common filesystem/process/network escapes, reflection, private attributes and direct GUI/document-lifecycle calls. Asset import uses `llmlink.import_asset(path)` and requires a 3D file explicitly attached to the current message. These checks are defense in depth. Generated Python still runs with Cinema 4D's user permissions; it is **not sandboxed**. A checkpoint cannot undo external file or system actions. Manual apply is the default for a new installation. Long native API calls cannot be interrupted by the ordinary Python time limit. Keep a separate backup of important work.

Prompts, scene metadata and supported attachments are sent to the provider you select. LM Studio requests stay on loopback; Codex uses your online account. There is no LLMLINK cloud service.

## Development

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tools/package.py
python3 tools/probe_providers.py --provider codex
python3 tools/probe_providers.py --provider lmstudio
```

`tests/c4d_native_workflow.py` must run in Cinema's Python console. It creates a separate test document and verifies scene edits, animation, checkpoints, asynchronous PNG rendering and save/reopen integrity. In the Cinema Python console, use `import runpy` followed by `llmlink_check = runpy.run_path("/absolute/path/LLMLINK_Cinema4D/tests/c4d_native_workflow.py")`; keep the returned dictionary alive while the test window is open. `docs/VALIDATION.md` records what was actually tested.

The command uses development Plugin ID `1069917`. Obtain a unique registered Maxon Plugin Cafe ID before public distribution; a local ID collision will prevent registration.

GPL-3.0-or-later. Copyright © 2026 Sergey Yemelin. Shared provider, transport and queue code was adapted from LLMLINK Blender commit `08438b3c5afd8ea8990b086468a0745857f95a65` (0.7.18). Independent project, not affiliated with Maxon, OpenAI or LM Studio.

API references: [Cinema 4D Python SDK](https://developers.maxon.net/docs/py/2024_3_0/), [Codex App Server](https://developers.openai.com/codex/app-server), [LM Studio model catalog](https://lmstudio.ai/docs/developer/rest/list).

## Long conversations

There is no request-count ceiling for a chat. The full local archive is separate from the model context. Repeated scene snapshots and verbose diagnostics are projected into a smaller planning view; older turns automatically become durable rolling memory. The latest request, measured scene and current repair code remain available. The model can retrieve earlier details with the read-only `recall` action. A new chat is not required when the working context fills. Memory summaries can be imperfect, so the current measured scene remains authoritative.

## Reliability audit

See [the 0.2.0 audit](docs/RELEASE_AUDIT.md) for fixes, measured verification and remaining public-release gates. Reconnect, queue crash recovery, draft preservation, provider output bounds and atomic private writes have regression coverage. Pending work is tied to its measured document. Draft updates use a small sidecar instead of rewriting the complete chat.
