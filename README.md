# FlowLens Task Mining

FlowLens is a local-first Windows task-mining MVP. It records foreground
applications, timestamps, window titles, time allocation, keystroke counts,
mouse-click counts, application switches, and optional screenshots.

It deliberately does **not** store typed characters, clipboard contents,
passwords, or form values.

## Run

```powershell
cd "C:\Users\Administrator\Desktop\Projects\Task Mining App"
python -m pip install -r requirements.txt
python TM.py
```
## Download
https://github.com/jmb-sagiwork/Task-Mining-App/raw/main/download/FlowLens-Task-Mining.exe

## Included

- Visible start, pause, resume, and stop controls
- Mandatory participant-consent confirmation
- Foreground application and window-title tracking
- Keystroke and mouse-click counts without key-content logging
- Configurable screenshots on application switch and/or schedule
- Local SQLite event store
- Session dashboard and per-application effort summary
- CSV event-log export for process-mining analysis
- Local evidence folders grouped by session

## Data

Runtime data is written under `task_mining_data/`:

- `task_mining.db` — sessions and activity events
- `screenshots/<session-id>/` — optional JPEG evidence

## Client-ready production roadmap

Before enterprise deployment, add:

1. Application and URL allow/deny policies.
2. OCR/vision redaction for passwords, PHI, PII, and payment data.
3. Encryption at rest and in transit with customer-managed keys.
4. Role-based access, SSO, tenant isolation, and immutable audit logs.
5. Signed endpoint agent packages, auto-update, and health monitoring.
6. Configurable retention, legal hold, deletion, and regional storage.
7. Central ingestion API and organization-level dashboards.
8. Task clustering, variant detection, rework/handoff analysis, and automation scoring.
9. Reviewer workflows for annotating, approving, and publishing discovered processes.

## Privacy note

Task mining can capture sensitive information. Use it only with informed
participant consent and an approved data-retention/access policy. Screenshots
should be disabled unless the engagement genuinely requires visual evidence.
