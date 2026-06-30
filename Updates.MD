# FlowLens Task Mining App Updates

## Codebase Summary

FlowLens is a local-first Windows task-mining MVP. It records foreground applications, window titles, timing, interaction counts, app switches, and optional screenshots, then stores the captured data locally for review and export.

The current app is implemented primarily in `TM.py`. Its main areas are:

- `WindowsContext`: reads foreground window and process metadata.
- `Database`: manages SQLite sessions and activity rows.
- `Recorder`: captures activity samples, interaction counts, app switches, and screenshots.
- `TaskMiningApp`: provides the Tkinter UI for recording, review, settings, evidence access, and exports.

Runtime data is stored in `task_mining_data/`, including `task_mining.db` and screenshot evidence. That directory is private runtime data and should not be committed or shared by default.

## Completed Updates

- Added process-flow generation from captured session activity.
- Added a `Generate flow text` action on the session review screen.
- Added a text process-map export that can be saved as `.txt` and opened in Notepad.
- Added flow report sections for:
  - session summary
  - process steps ranked by observed time
  - common step transitions
  - likely bottlenecks
  - loop or rework signals
  - chronological flow
- Added basic masking for likely sensitive window-title values, including email addresses and long numeric identifiers.
- Added this update document to describe the codebase, implemented changes, and next priorities.

## Main Product Direction

The point of this tool is task mining: captured activity should become a useful process flow. The app should help an analyst move from raw desktop observation to a readable workflow map that shows what happened, where time went, which transitions repeated, and where bottlenecks or rework appeared.

The first flow output is intentionally a text process map instead of a graphical model. This keeps the output easy to inspect, save, and share while the flow-mining rules mature.

## Recommended Next Updates

1. Improve flow mining accuracy.
   - Merge noisy window-title changes into stable task steps.
   - Add configurable grouping rules by app, title pattern, duration, and idle time.
   - Add stronger loop, handoff, wait-time, and rework detection.

2. Add a graphical process map.
   - Render nodes for steps and edges for transitions.
   - Size nodes by duration and edges by transition frequency.
   - Allow export to PNG, SVG, or BPMN-like documentation.

3. Strengthen privacy and compliance.
   - Add first-run consent and data-handling onboarding.
   - Make screenshots explicitly opt-in for sensitive workflows.
   - Add app/process deny lists.
   - Add stronger redaction before persistence and export.
   - Add retention controls and a delete-all-captured-data action.

4. Add tests before larger refactors.
   - Test flow grouping, transition counts, duration totals, loop detection, and text export.
   - Test database writes and reads.
   - Test recorder start/stop cleanup.
   - Test screenshot-disabled behavior.

5. Split the monolithic source file.
   - Move storage into `flowlens/storage.py`.
   - Move recording into `flowlens/recorder.py`.
   - Move flow mining into `flowlens/flow_mining.py`.
   - Move export logic into `flowlens/export.py`.
   - Keep the UI layer focused on rendering and user actions.

6. Improve packaging and releases.
   - Add a Python package layout and `pyproject.toml`.
   - Keep `run.ps1` as a convenience launcher.
   - Build downloadable Windows executables for release.
   - Publish release notes with every downloadable build.

## Release Notes For This Build

- Added generated task-flow text reports.
- Added Notepad-friendly `.txt` output for selected sessions.
- Added basic privacy masking in flow reports.
- Added a visible update roadmap in `Updates.MD`.
- Kept captured runtime data excluded from version control.
