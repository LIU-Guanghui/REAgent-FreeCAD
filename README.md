# REAgent-FreeCAD

English | [简体中文](docs/README.zh-CN.md)

**Reconstruct STEP parts as editable FreeCAD models with an agent.**

REAgent-FreeCAD turns single-solid STEP/STP parts into native FreeCAD feature histories. An agent inspects the reference in a read-only Source window, builds in a separate Target window, and compares the result as it works. The project provides a local MCP server, the REAgent workbench for FreeCAD, and a reconstruction Skill.

## Features

- **Inspect the reference:** examine geometry, dimensions, and views before choosing a construction.
- **Build editable features:** create native PartDesign features with parameters.
- **Compare and refine:** use geometric comparisons and visual evidence to guide the next modeling step.

## Installation and configuration

You need FreeCAD 1.1, Python 3.10 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/), and an agent client that supports local stdio MCP servers and images. The examples below use Windows and Codex. Run all PowerShell commands from the repository root. `~` means the current user's directory, such as `C:\Users\your-name`.

### 1. Install the MCP server

Download or clone this repository, then run the following from its root:

```powershell
uv sync --locked
```

This installs the project and its locked dependencies into `.venv`. Keep the repository at this location; the client configuration below uses its launcher.

### 2. Install the FreeCAD workbench

Close FreeCAD, then copy the repository's `addon/REAgent/` folder into the `Mod` folder under your FreeCAD installation directory. The example below uses `C:\Program Files\FreeCAD 1.1`; adjust it to match your installation path. Writing to `Program Files` may require administrator privileges:

```powershell
Copy-Item -LiteralPath '.\addon\REAgent' -Destination 'C:\Program Files\FreeCAD 1.1\Mod' -Recurse -Force
```

The resulting path should be `<FreeCAD installation directory>/Mod/REAgent/InitGui.py`. Reopen FreeCAD; **REAgent** will appear in the workbench selector.

### 3. Connect the agent client

For Codex, add the following to `~/.codex/config.toml`, replacing the example path in `command` with the absolute path to your repository:

```toml
[mcp_servers.REAgentFreeCAD]
command = "C:/path/to/REAgentFreeCAD/.venv/Scripts/REAgentFreeCAD.exe"
```

Reload the client and check that the server is available.

### 4. Make the reconstruction Skill available

For Codex, copy the entire `.agents/skills/reagentfreecad/` folder, including `references/` and `agents/`, into `~/.agents/skills/reagentfreecad/`:

```powershell
New-Item -ItemType Directory -Path '~/.agents/skills' -Force | Out-Null
Copy-Item -LiteralPath '.\.agents\skills\reagentfreecad' -Destination '~/.agents/skills' -Recurse -Force
```

Alternatively, use this repository as your Codex working directory to load the bundled [reconstruction Skill](.agents/skills/reagentfreecad/SKILL.md).

## Quick start

1. Open two separate FreeCAD instances and select the **REAgent** workbench in each.
2. Click **Source** in one window and **Target** in the other. Keep both windows visible. Their status labels show the assigned roles; **Stop** releases a role when you finish.
3. In your agent client, provide the STEP file path and where to save the result. For example:

   ```text
   Use $reagentfreecad to reconstruct C:/parts/source.step and save the result in the current directory.
   ```

4. Follow the construction in Target. After the agent finishes, inspect the feature tree and comparison results, then open the saved `.FCStd` file in FreeCAD to edit the model.

## Tool reference

There are currently 55 public MCP tools, listed below by name and purpose. Query `agent_get_capabilities` for parameters. See [tools_schema.py](src/REAgentFreeCAD/tools_schema.py) for the tool definitions.

### Reconstruction management

| Tool | Purpose |
| --- | --- |
| `agent_ping` | Check Source and Target connectivity |
| `agent_get_capabilities` | Query the tool catalog, parameter schemas, and runtime capabilities |
| `agent_get_guidance` | Read reconstruction knowledge cards and their catalog |
| `agent_start_session` | Start a session from STEP or resume an existing FCStd reconstruction |
| `agent_get_state` | Read session state, model objects, and asynchronous operation progress and results |
| `agent_observe` | Get geometry, topology, dimensions, sections, and view evidence |
| `agent_validate` | Check model structure and optionally compare geometry or audit parameters |
| `agent_undo` | Undo a session-owned REAgent transaction at the top of the native history stack |
| `agent_redo` | Redo a session-owned REAgent transaction at the top of the native history stack |
| `agent_save` | Save the Target model as FCStd at the session's allocated path |
| `agent_close_session` | Explicitly discard unsaved changes and close this session's documents |
| `agent_journal` | Read or flush the session journal |
| `agent_read_freecad_log` | Read the selected FreeCAD instance's native log |

### Native modeling

| Tool | Purpose |
| --- | --- |
| `PartDesign_Body` | Create a Body |
| `PartDesign_NewSketch`, `Sketcher_MapSketch`, `Sketcher_EditSketch`, `Sketcher_ValidateSketch` | Create, attach, edit, and check sketches |
| `Part_DatumPlane`, `Part_DatumLine`, `Part_DatumPoint`, `Part_CoordinateSystem` | Create datum planes, lines, points, and coordinate systems |
| `PartDesign_AdditiveBox`, `PartDesign_SubtractiveBox`<br>`PartDesign_AdditiveCylinder`, `PartDesign_SubtractiveCylinder`<br>`PartDesign_AdditiveCone`, `PartDesign_SubtractiveCone`<br>`PartDesign_AdditiveSphere`, `PartDesign_SubtractiveSphere`<br>`PartDesign_AdditiveEllipsoid`, `PartDesign_SubtractiveEllipsoid`<br>`PartDesign_AdditiveTorus`, `PartDesign_SubtractiveTorus`<br>`PartDesign_AdditivePrism`, `PartDesign_SubtractivePrism`<br>`PartDesign_AdditiveWedge`, `PartDesign_SubtractiveWedge` | Create additive or subtractive primitives: Box, Cylinder, Cone, Sphere, Ellipsoid, Torus, Prism, and Wedge |
| `PartDesign_Pad`, `PartDesign_Pocket` | Pad and Pocket |
| `PartDesign_Revolution`, `PartDesign_Groove` | Revolution and Groove |
| `PartDesign_AdditiveLoft`, `PartDesign_SubtractiveLoft` | Additive and subtractive loft |
| `PartDesign_AdditivePipe`, `PartDesign_SubtractivePipe` | Additive and subtractive sweep |
| `PartDesign_Hole` | Hole |
| `PartDesign_Boolean` | Boolean |
| `PartDesign_Fillet` | Fillet |
| `PartDesign_Chamfer` | Chamfer |
| `PartDesign_Draft` | Draft |
| `PartDesign_Thickness` | Thickness |
| `PartDesign_Mirrored` | Mirror |
| `PartDesign_LinearPattern`, `PartDesign_PolarPattern` | Linear and polar patterns |

## Project structure

All project files in the repository are listed below, with their main responsibilities.

```text
REAgentFreeCAD/
├── .agents/
│   └── skills/
│       └── reagentfreecad/                             # Reconstruction Skill
│           ├── agents/                                 # Client metadata
│           │   └── openai.yaml                         # Skill presentation and client metadata
│           ├── references/                             # Reference guides loaded as needed
│           │   ├── features/                           # Feature modeling guides
│           │   │   ├── body.md                         # Body guide
│           │   │   ├── boolean.md                      # Boolean guide
│           │   │   ├── chamfer.md                      # Chamfer guide
│           │   │   ├── datum.md                        # Datum guide
│           │   │   ├── draft.md                        # Draft guide
│           │   │   ├── entry.md                        # Feature guide entry
│           │   │   ├── extrude.md                      # Pad and Pocket guide
│           │   │   ├── fillet.md                       # Fillet guide
│           │   │   ├── hole.md                         # Hole guide
│           │   │   ├── loft.md                         # Loft guide
│           │   │   ├── mirror.md                       # Mirror guide
│           │   │   ├── pattern.md                      # Pattern guide
│           │   │   ├── pipe.md                         # Pipe guide
│           │   │   ├── primitives.md                   # Primitive guide
│           │   │   ├── revolve.md                      # Revolution and Groove guide
│           │   │   ├── sketch.md                       # Sketch guide
│           │   │   └── thickness.md                    # Thickness guide
│           │   ├── foundations/                        # FreeCAD foundations
│           │   │   ├── brep-topology.md                # B-Rep geometry and topology
│           │   │   ├── entry.md                        # Foundations entry
│           │   │   ├── freecad-document.md             # FreeCAD documents and object model
│           │   │   ├── geometry-parameterization.md    # Geometry parameterization
│           │   │   ├── persistent-references.md        # Persistent references and topology changes
│           │   │   ├── tolerance-validity.md           # Tolerances and geometry validity
│           │   │   └── transactions-errors.md          # Transactions, errors, and recovery
│           │   └── workflow/                           # Reconstruction workflow guides
│           │       ├── entry.md                        # Reconstruction workflow entry
│           │       ├── observation.md                  # Source and Target observation
│           │       ├── session-and-isolation.md        # Session isolation and recovery
│           │       └── validation.md                   # Result validation and saving
│           └── SKILL.md                                # Reconstruction Skill entry and operating rules
├── addon/
│   └── REAgent/                                        # FreeCAD workbench and local bridge
│       ├── analysis/                                   # Read-only geometry analysis and evidence
│       │   ├── __init__.py                             # Package initialization
│       │   ├── evidence.py                             # B-Rep evidence, relations, and selectors
│       │   ├── math3d.py                               # Numeric, vector, and topology name helpers
│       │   ├── measurements.py                         # Geometry bounds and measurements
│       │   ├── predecessor.py                          # Read-only inference of predecessor bounds
│       │   ├── references.py                           # Geometry reference schemas and resolution
│       │   ├── sampling.py                             # Trimmed-face point and normal sampling
│       │   ├── selection.py                            # Geometry queries and current subelement resolution
│       │   └── topology.py                             # Topology details, fingerprints, and relations
│       ├── features/                                   # Native feature implementations
│       │   ├── common/                                 # Shared construction and reference logic
│       │   │   ├── __init__.py                         # Package initialization
│       │   │   ├── construction.py                     # Body construction, preflight, and result readback
│       │   │   ├── references.py                       # Native references and link readback
│       │   │   └── transforms.py                       # Mirror and pattern operands and references
│       │   ├── __init__.py                             # Package initialization
│       │   ├── body.py                                 # Native Body implementation
│       │   ├── boolean.py                              # Native Boolean implementation
│       │   ├── chamfer.py                              # Native Chamfer implementation
│       │   ├── datum.py                                # Native Datum implementation
│       │   ├── draft.py                                # Native Draft implementation
│       │   ├── extrude.py                              # Native Pad and Pocket implementation
│       │   ├── fillet.py                               # Native Fillet implementation
│       │   ├── hole.py                                 # Native Hole implementation
│       │   ├── loft.py                                 # Native Loft implementation
│       │   ├── mirror.py                               # Native Mirror implementation
│       │   ├── pattern.py                              # Native Pattern implementation
│       │   ├── pipe.py                                 # Native Pipe implementation
│       │   ├── primitives.py                           # Native Primitive implementation
│       │   ├── revolve.py                              # Native Revolution and Groove implementation
│       │   ├── sketch.py                               # Native Sketch implementation
│       │   └── thickness.py                            # Native Thickness implementation
│       ├── protocol/                                   # Bridge protocol and shared contracts
│       │   ├── __init__.py                             # Package initialization
│       │   ├── capabilities.py                         # Bridge capability discovery
│       │   ├── contract.py                             # Shared operation, role, and parameter contracts
│       │   ├── logs.py                                 # Bound instance native log reads
│       │   ├── responses.py                            # Success and failure response metadata
│       │   └── server.py                               # JSON Lines transport and GUI thread dispatch
│       ├── validation/                                 # Model structure and geometry validation
│       │   ├── __init__.py                             # Package initialization
│       │   ├── comparison.py                           # Measurements, surface sampling, and directional Booleans
│       │   ├── conclusions.py                          # Structure and parameter probe facts
│       │   ├── handlers.py                             # Target readback and requested diagnostics
│       │   ├── parameters.py                           # Parameter perturbation and restoration checks
│       │   ├── structure.py                            # Native structure and single-solid readiness checks
│       │   └── tolerance.py                            # Geometry reference and diagnostic tolerances
│       ├── view/                                       # Views, cameras, and presentation
│       │   ├── __init__.py                             # Package initialization
│       │   ├── camera.py                               # Camera poses, readback, and restoration
│       │   ├── presentation.py                         # Object presentation and visible window state
│       │   ├── queries.py                              # Source and Target view planning
│       │   └── rendering.py                            # View rendering, image artifacts, and composition
│       ├── workspace/                                  # Documents, sessions, and recovery
│       │   ├── __init__.py                             # Package initialization
│       │   ├── executor.py                             # Native transactions, recovery, and result state
│       │   ├── model.py                                # Workspace ownership and result selection
│       │   ├── persistence.py                          # FCStd persistence and path protection
│       │   ├── properties.py                           # Property metadata, codecs, and Sketch readback
│       │   ├── queries.py                              # Role-local geometry observation and selection queries
│       │   ├── recovery.py                             # Recovery evidence, write blocking, and history protection
│       │   ├── scope.py                                # Observation scope and restoration confirmation
│       │   ├── sessions.py                             # Native workspace creation and resumption
│       │   └── state.py                                # Model, geometry, and revision readback
│       ├── __init__.py                                 # Workbench package initialization
│       ├── bridge.py                                   # Local bridge and role binding entry
│       ├── Init.py                                     # FreeCAD application initialization
│       ├── InitGui.py                                  # Workbench and role binding commands
│       ├── package.xml                                 # Workbench package metadata
│       └── runtime.py                                  # FreeCAD dependencies, process state, and helpers
├── docs/                                               # User documentation
│   └── README.zh-CN.md                                 # Chinese README
├── src/
│   └── REAgentFreeCAD/                                 # Local MCP server
│       ├── features/                                   # Modeling tool adapters
│       │   ├── __init__.py                             # Package initialization
│       │   ├── body.py                                 # Body adapter and evidence checks
│       │   ├── boolean.py                              # Boolean adapter and evidence checks
│       │   ├── chamfer.py                              # Chamfer adapter and evidence checks
│       │   ├── datum.py                                # Datum adapter and evidence checks
│       │   ├── draft.py                                # Draft adapter and evidence checks
│       │   ├── execution.py                            # Shared session and evidence checks for feature adapters
│       │   ├── extrude.py                              # Pad and Pocket adapter and evidence checks
│       │   ├── fillet.py                               # Fillet adapter and evidence checks
│       │   ├── hole.py                                 # Hole adapter and evidence checks
│       │   ├── loft.py                                 # Loft adapter and evidence checks
│       │   ├── mirror.py                               # Mirror adapter and evidence checks
│       │   ├── pattern.py                              # Pattern adapter and evidence checks
│       │   ├── pipe.py                                 # Pipe adapter and evidence checks
│       │   ├── primitives.py                           # Primitive adapter and evidence checks
│       │   ├── revolve.py                              # Revolution and Groove adapter and evidence checks
│       │   ├── sketch.py                               # Sketch adapter and evidence checks
│       │   └── thickness.py                            # Thickness adapter and evidence checks
│       ├── workflow/                                   # Reconstruction management workflows
│       │   ├── __init__.py                             # Package initialization
│       │   ├── context.py                              # Session state, guarded bridge calls, and journal coordination
│       │   ├── diagnostics.py                          # Native log diagnostics workflow
│       │   ├── discovery.py                            # Tool capability and knowledge discovery
│       │   ├── history.py                              # Session-owned native undo and redo
│       │   ├── observation.py                          # Source and Target observation evidence adapters
│       │   ├── session.py                              # Two-window session lifecycle
│       │   └── validation.py                           # Validation facts, dependency revisions, and saving
│       ├── __init__.py                                 # Package initialization and lazy entry point
│       ├── __main__.py                                 # Python module entry point
│       ├── artifacts.py                                # Model and journal artifact naming and allocation
│       ├── bootstrap.py                                # MCP startup and dependency diagnostics
│       ├── client.py                                   # FreeCAD JSON Lines bridge client
│       ├── contract.py                                 # Shared interface contract loader
│       ├── errors.py                                   # Public error structures and conversion
│       ├── execution.py                                # Serialized background execution and outcome lookup
│       ├── journal.py                                  # Reconstruction session journal
│       ├── knowledge.py                                # Skill and knowledge card resource access
│       ├── log_summary.py                              # Compact request, result, and error logging
│       ├── results.py                                  # JSON-safe bridge results and errors
│       ├── server.py                                   # MCP stdio server and tool dispatch
│       ├── tools_schema.py                             # Public MCP tools and parameter schemas
│       └── view_delivery.py                            # View image preparation and MCP delivery
├── .gitignore                                          # Local file ignore rules
├── LICENSE                                             # MIT license
├── pyproject.toml                                      # Project metadata, dependencies, and entry point
├── README.md                                           # English README
└── uv.lock                                             # Locked dependency versions
```

## License

REAgent-FreeCAD is licensed under the [MIT License](LICENSE), except where third-party notices state otherwise.
