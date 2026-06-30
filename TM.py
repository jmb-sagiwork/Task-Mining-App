from __future__ import annotations

import csv
import ctypes
import json
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import BooleanVar, IntVar, StringVar, Tk, filedialog, messagebox
from tkinter import ttk

try:
    from PIL import ImageGrab
except ImportError:
    ImageGrab = None

try:
    from pynput import keyboard, mouse
except ImportError:
    keyboard = None
    mouse = None


APP_NAME = "FlowLens Task Mining"
APP_VERSION = "0.1.0"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "task_mining_data"
SCREENSHOT_DIR = DATA_DIR / "screenshots"
DB_PATH = DATA_DIR / "task_mining.db"


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def format_seconds(value: float | int) -> str:
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def safe_filename(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in "-_." else "_" for char in value)
    return cleaned[:80] or "capture"


@dataclass
class FlowStep:
    label: str
    application: str
    window_hint: str
    event_count: int = 0
    duration_seconds: float = 0.0
    keystrokes: int = 0
    mouse_clicks: int = 0


@dataclass
class FlowTransition:
    source: str
    target: str
    count: int = 0
    duration_seconds: float = 0.0


def redact_window_title(value: str) -> str:
    """Keep flow reports useful without exporting likely identifiers."""
    if not value:
        return "Untitled window"
    redacted = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[email]", value)
    redacted = re.sub(r"\b\d{4,}\b", "[number]", redacted)
    redacted = re.sub(r"\s+", " ", redacted).strip()
    return redacted[:90] or "Untitled window"


def flow_label(application: str, window_title: str) -> tuple[str, str]:
    app = application or "Unknown"
    hint = redact_window_title(window_title)
    if hint and hint != "Untitled window":
        return f"{app}: {hint}", hint
    return app, hint


def build_flow_report(session: sqlite3.Row, rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "\n".join(
            [
                f"FlowLens Task Flow - {session['name']}",
                "",
                "No activity rows were captured for this session.",
            ]
        )

    steps: dict[str, FlowStep] = {}
    transitions: dict[tuple[str, str], FlowTransition] = {}
    ordered_labels: list[str] = []
    previous_label: str | None = None
    switch_count = 0

    for row in rows:
        label, hint = flow_label(row["application"], row["window_title"])
        duration = float(row["duration_seconds"] or 0)
        step = steps.setdefault(label, FlowStep(label=label, application=row["application"] or "Unknown", window_hint=hint))
        step.event_count += 1
        step.duration_seconds += duration
        step.keystrokes += int(row["keystrokes"] or 0)
        step.mouse_clicks += int(row["mouse_clicks"] or 0)

        if label != previous_label:
            ordered_labels.append(label)
            if previous_label:
                key = (previous_label, label)
                transition = transitions.setdefault(key, FlowTransition(source=previous_label, target=label))
                transition.count += 1
                transition.duration_seconds += duration
                switch_count += 1
            previous_label = label

    total_duration = float(session["duration_seconds"] or sum(row["duration_seconds"] or 0 for row in rows))
    total_interactions = int(session["keystrokes"] or 0) + int(session["mouse_clicks"] or 0)
    ranked_steps = sorted(steps.values(), key=lambda item: item.duration_seconds, reverse=True)
    ranked_transitions = sorted(transitions.values(), key=lambda item: (item.count, item.duration_seconds), reverse=True)
    loops = [transition for transition in ranked_transitions if transition.source == transition.target]
    repeated_returns = [
        transition
        for transition in ranked_transitions
        if transitions.get((transition.target, transition.source)) and transition.source != transition.target
    ]
    bottlenecks = [step for step in ranked_steps if step.duration_seconds >= max(60, total_duration * 0.2)]

    lines = [
        f"FlowLens Task Flow - {session['name']}",
        "=" * 72,
        f"Participant: {session['user_label']}",
        f"Started: {session['started_at']}",
        f"Ended: {session['ended_at'] or 'Not recorded'}",
        f"Observed time: {format_seconds(total_duration)}",
        f"Activity samples: {len(rows):,}",
        f"Unique flow steps: {len(steps):,}",
        f"App/window transitions: {switch_count:,}",
        f"Interactions: {total_interactions:,}",
        "",
        "Privacy note: window titles are masked for long numbers and email addresses.",
        "Review this report before sharing because app names and remaining titles may still be sensitive.",
        "",
        "Process steps by observed time",
        "-" * 72,
    ]
    for index, step in enumerate(ranked_steps, start=1):
        lines.append(
            f"{index}. {step.label} | {format_seconds(step.duration_seconds)} | "
            f"{step.event_count:,} samples | {step.keystrokes:,} keys | {step.mouse_clicks:,} clicks"
        )

    lines.extend(["", "Common transitions", "-" * 72])
    if ranked_transitions:
        for index, transition in enumerate(ranked_transitions[:20], start=1):
            average = transition.duration_seconds / max(1, transition.count)
            lines.append(
                f"{index}. {transition.source} -> {transition.target} | "
                f"{transition.count:,} times | avg next-step time {format_seconds(average)}"
            )
    else:
        lines.append("No transitions detected.")

    lines.extend(["", "Likely bottlenecks", "-" * 72])
    if bottlenecks:
        for step in bottlenecks[:10]:
            share = (step.duration_seconds / total_duration * 100) if total_duration else 0
            lines.append(f"- {step.label}: {format_seconds(step.duration_seconds)} ({share:.1f}% of observed time)")
    else:
        lines.append("No single step dominated the observed time.")

    lines.extend(["", "Loop / rework signals", "-" * 72])
    if loops:
        for transition in loops[:10]:
            lines.append(f"- Repeated same step: {transition.source} ({transition.count:,} times)")
    if repeated_returns:
        for transition in repeated_returns[:10]:
            reverse = transitions[(transition.target, transition.source)]
            lines.append(
                f"- Back-and-forth: {transition.source} <-> {transition.target} "
                f"({transition.count + reverse.count:,} transitions)"
            )
    if not loops and not repeated_returns:
        lines.append("No repeated loop pattern detected in this session.")

    lines.extend(["", "Chronological flow", "-" * 72])
    compressed_flow: list[str] = []
    for label in ordered_labels:
        if not compressed_flow or compressed_flow[-1] != label:
            compressed_flow.append(label)
    for index, label in enumerate(compressed_flow[:80], start=1):
        lines.append(f"{index}. {label}")
    if len(compressed_flow) > 80:
        lines.append(f"... {len(compressed_flow) - 80:,} additional transitions omitted for readability.")

    return "\n".join(lines) + "\n"


@dataclass
class ActiveWindow:
    application: str = "Unknown"
    title: str = "Unknown window"
    process_id: int = 0


class WindowsContext:
    """Reads foreground-window metadata without collecting typed content."""

    def __init__(self) -> None:
        self.user32 = ctypes.windll.user32 if os.name == "nt" else None
        self.kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None

    def active_window(self) -> ActiveWindow:
        if not self.user32:
            return ActiveWindow(application="Unsupported OS")

        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return ActiveWindow()

        length = self.user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buffer, length + 1)

        pid = ctypes.c_ulong()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        application = self._process_name(pid.value)
        return ActiveWindow(
            application=application,
            title=buffer.value or application,
            process_id=pid.value,
        )

    def _process_name(self, pid: int) -> str:
        if not pid:
            return "Unknown"
        try:
            import psutil

            return psutil.Process(pid).name()
        except Exception:
            return f"Process {pid}"


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.Lock()
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    user_label TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    duration_seconds INTEGER NOT NULL DEFAULT 0,
                    keystrokes INTEGER NOT NULL DEFAULT 0,
                    mouse_clicks INTEGER NOT NULL DEFAULT 0,
                    app_switches INTEGER NOT NULL DEFAULT 0,
                    screenshots INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'recording',
                    settings_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS activities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    application TEXT NOT NULL,
                    window_title TEXT NOT NULL,
                    process_id INTEGER NOT NULL DEFAULT 0,
                    duration_seconds REAL NOT NULL DEFAULT 0,
                    keystrokes INTEGER NOT NULL DEFAULT 0,
                    mouse_clicks INTEGER NOT NULL DEFAULT 0,
                    screenshot_path TEXT,
                    event_type TEXT NOT NULL DEFAULT 'activity',
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_activities_session
                ON activities(session_id, captured_at);
                """
            )

    def start_session(self, name: str, user_label: str, settings: dict) -> str:
        session_id = str(uuid.uuid4())
        with self.lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    id, name, user_label, started_at, status, settings_json
                ) VALUES (?, ?, ?, ?, 'recording', ?)
                """,
                (session_id, name, user_label, now_iso(), json.dumps(settings)),
            )
        return session_id

    def add_activity(self, activity: dict) -> None:
        with self.lock, self.connect() as connection:
            connection.execute(
                """
                INSERT INTO activities (
                    session_id, captured_at, application, window_title,
                    process_id, duration_seconds, keystrokes, mouse_clicks,
                    screenshot_path, event_type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    activity["session_id"],
                    activity["captured_at"],
                    activity["application"],
                    activity["window_title"],
                    activity["process_id"],
                    activity["duration_seconds"],
                    activity["keystrokes"],
                    activity["mouse_clicks"],
                    activity.get("screenshot_path"),
                    activity.get("event_type", "activity"),
                ),
            )

    def update_session(self, session_id: str, metrics: dict, status: str = "recording") -> None:
        with self.lock, self.connect() as connection:
            connection.execute(
                """
                UPDATE sessions SET
                    duration_seconds = ?,
                    keystrokes = ?,
                    mouse_clicks = ?,
                    app_switches = ?,
                    screenshots = ?,
                    status = ?,
                    ended_at = CASE WHEN ? = 'completed' THEN ? ELSE ended_at END
                WHERE id = ?
                """,
                (
                    metrics["duration_seconds"],
                    metrics["keystrokes"],
                    metrics["mouse_clicks"],
                    metrics["app_switches"],
                    metrics["screenshots"],
                    status,
                    status,
                    now_iso(),
                    session_id,
                ),
            )

    def sessions(self) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT * FROM sessions
                    ORDER BY started_at DESC
                    """
                )
            )

    def session(self, session_id: str) -> sqlite3.Row | None:
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()

    def activities(self, session_id: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT * FROM activities
                    WHERE session_id = ?
                    ORDER BY captured_at
                    """,
                    (session_id,),
                )
            )

    def app_summary(self, session_id: str) -> list[sqlite3.Row]:
        with self.connect() as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        application,
                        ROUND(SUM(duration_seconds), 1) AS duration_seconds,
                        SUM(keystrokes) AS keystrokes,
                        SUM(mouse_clicks) AS mouse_clicks,
                        COUNT(*) AS activity_samples
                    FROM activities
                    WHERE session_id = ?
                    GROUP BY application
                    ORDER BY duration_seconds DESC
                    """,
                    (session_id,),
                )
            )


class Recorder:
    def __init__(self, database: Database, events: queue.Queue) -> None:
        self.database = database
        self.events = events
        self.context = WindowsContext()
        self.session_id: str | None = None
        self.running = False
        self.paused = False
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.keyboard_listener = None
        self.mouse_listener = None
        self.lock = threading.Lock()
        self.started_monotonic = 0.0
        self.paused_total = 0.0
        self.pause_started = 0.0
        self.last_sample = 0.0
        self.last_screenshot = 0.0
        self.current_window = ActiveWindow()
        self.pending_keystrokes = 0
        self.pending_clicks = 0
        self.metrics = {
            "duration_seconds": 0,
            "keystrokes": 0,
            "mouse_clicks": 0,
            "app_switches": 0,
            "screenshots": 0,
        }
        self.settings: dict = {}

    def start(self, name: str, user_label: str, settings: dict) -> str:
        if self.running:
            raise RuntimeError("A recording is already active.")
        if keyboard is None or mouse is None:
            raise RuntimeError("pynput is not installed. Run: pip install -r requirements.txt")

        self.settings = settings
        self.session_id = self.database.start_session(name, user_label, settings)
        self.running = True
        self.paused = False
        self.stop_event.clear()
        self.started_monotonic = time.monotonic()
        self.paused_total = 0
        self.last_sample = self.started_monotonic
        self.last_screenshot = self.started_monotonic
        self.current_window = self.context.active_window()
        self.pending_keystrokes = 0
        self.pending_clicks = 0
        self.metrics = {key: 0 for key in self.metrics}

        self.keyboard_listener = keyboard.Listener(on_press=self._on_key_press)
        self.mouse_listener = mouse.Listener(on_click=self._on_click)
        self.keyboard_listener.start()
        self.mouse_listener.start()

        self.worker = threading.Thread(target=self._record_loop, daemon=True)
        self.worker.start()
        self.events.put(("status", "Recording"))
        return self.session_id

    def toggle_pause(self) -> bool:
        if not self.running:
            return False
        if self.paused:
            self.paused_total += time.monotonic() - self.pause_started
            self.last_sample = time.monotonic()
            self.paused = False
            self.events.put(("status", "Recording"))
        else:
            self._flush_sample("pause")
            self.pause_started = time.monotonic()
            self.paused = True
            self.events.put(("status", "Paused"))
        return self.paused

    def stop(self) -> None:
        if not self.running:
            return
        if self.paused:
            self.paused_total += time.monotonic() - self.pause_started
            self.paused = False
        self._flush_sample("stop")
        self.running = False
        self.stop_event.set()
        for listener in (self.keyboard_listener, self.mouse_listener):
            if listener:
                listener.stop()
        if self.session_id:
            self._update_duration()
            self.database.update_session(self.session_id, self.metrics, "completed")
        self.events.put(("status", "Completed"))
        self.events.put(("session_completed", self.session_id))

    def _on_key_press(self, _key) -> None:
        if self.running and not self.paused:
            with self.lock:
                self.pending_keystrokes += 1
                self.metrics["keystrokes"] += 1

    def _on_click(self, _x, _y, _button, pressed: bool) -> None:
        if self.running and not self.paused and pressed:
            with self.lock:
                self.pending_clicks += 1
                self.metrics["mouse_clicks"] += 1

    def _record_loop(self) -> None:
        sample_interval = max(1, int(self.settings.get("sample_interval", 2)))
        while not self.stop_event.wait(0.25):
            if not self.running or self.paused:
                continue

            current = self.context.active_window()
            window_changed = (
                current.application != self.current_window.application
                or current.title != self.current_window.title
            )
            if window_changed:
                self._flush_sample("app_switch")
                self.current_window = current
                with self.lock:
                    self.metrics["app_switches"] += 1
                if self.settings.get("screenshot_on_switch", True):
                    self._capture_screenshot("switch")

            now = time.monotonic()
            if now - self.last_sample >= sample_interval:
                self._flush_sample("activity")

            screenshot_interval = int(self.settings.get("screenshot_interval", 30))
            if (
                self.settings.get("screenshots_enabled", True)
                and screenshot_interval > 0
                and now - self.last_screenshot >= screenshot_interval
            ):
                self._capture_screenshot("interval")

            self._update_duration()
            if self.session_id:
                self.database.update_session(self.session_id, self.metrics)
            self.events.put(("metrics", dict(self.metrics)))
            self.events.put(("window", self.current_window))

    def _update_duration(self) -> None:
        if not self.started_monotonic:
            return
        end = self.pause_started if self.paused else time.monotonic()
        self.metrics["duration_seconds"] = max(
            0, int(end - self.started_monotonic - self.paused_total)
        )

    def _flush_sample(self, event_type: str) -> None:
        if not self.session_id or not self.last_sample:
            return
        now = time.monotonic()
        duration = max(0, now - self.last_sample)
        with self.lock:
            keys = self.pending_keystrokes
            clicks = self.pending_clicks
            self.pending_keystrokes = 0
            self.pending_clicks = 0
        if duration < 0.05 and keys == 0 and clicks == 0:
            return
        self.database.add_activity(
            {
                "session_id": self.session_id,
                "captured_at": now_iso(),
                "application": self.current_window.application,
                "window_title": self.current_window.title,
                "process_id": self.current_window.process_id,
                "duration_seconds": round(duration, 3),
                "keystrokes": keys,
                "mouse_clicks": clicks,
                "event_type": event_type,
            }
        )
        self.last_sample = now

    def _capture_screenshot(self, reason: str) -> None:
        if (
            not self.settings.get("screenshots_enabled", True)
            or ImageGrab is None
            or not self.session_id
        ):
            return
        try:
            session_dir = SCREENSHOT_DIR / self.session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = f"{stamp}_{safe_filename(self.current_window.application)}_{reason}.jpg"
            path = session_dir / filename
            image = ImageGrab.grab(all_screens=True)
            quality = int(self.settings.get("screenshot_quality", 70))
            image.convert("RGB").save(path, "JPEG", quality=quality, optimize=True)
            with self.lock:
                self.metrics["screenshots"] += 1
            self.database.add_activity(
                {
                    "session_id": self.session_id,
                    "captured_at": now_iso(),
                    "application": self.current_window.application,
                    "window_title": self.current_window.title,
                    "process_id": self.current_window.process_id,
                    "duration_seconds": 0,
                    "keystrokes": 0,
                    "mouse_clicks": 0,
                    "screenshot_path": str(path),
                    "event_type": f"screenshot_{reason}",
                }
            )
            self.last_screenshot = time.monotonic()
            self.events.put(("screenshot", str(path)))
        except Exception as exc:
            self.events.put(("error", f"Screenshot capture failed: {exc}"))


class TaskMiningApp:
    COLORS = {
        "navy": "#0B1220",
        "panel": "#111B2E",
        "panel2": "#17243B",
        "cyan": "#35D0BA",
        "blue": "#4A8CFF",
        "text": "#F5F7FA",
        "muted": "#94A3B8",
        "danger": "#FF6B6B",
        "warning": "#F6C85F",
        "white": "#FFFFFF",
    }

    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("1220x760")
        self.root.minsize(1040, 680)
        self.root.configure(bg=self.COLORS["navy"])

        self.database = Database(DB_PATH)
        self.event_queue: queue.Queue = queue.Queue()
        self.recorder = Recorder(self.database, self.event_queue)
        self.selected_session_id: str | None = None

        self.session_name = StringVar(value=f"Discovery {datetime.now():%Y-%m-%d}")
        self.user_label = StringVar(value=os.environ.get("USERNAME", "Participant"))
        self.status = StringVar(value="Ready")
        self.active_app = StringVar(value="No recording active")
        self.active_title = StringVar(value="Press Start recording to begin")
        self.elapsed = StringVar(value="00:00")
        self.keystrokes = StringVar(value="0")
        self.clicks = StringVar(value="0")
        self.switches = StringVar(value="0")
        self.screenshots = StringVar(value="0")
        self.screenshots_enabled = BooleanVar(value=True)
        self.screenshot_on_switch = BooleanVar(value=True)
        self.screenshot_interval = IntVar(value=30)
        self.sample_interval = IntVar(value=2)
        self.screenshot_quality = IntVar(value=70)
        self.consent = BooleanVar(value=False)

        self._configure_styles()
        self._build_ui()
        self._refresh_sessions()
        self.root.after(200, self._process_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("App.TFrame", background=self.COLORS["navy"])
        style.configure("Panel.TFrame", background=self.COLORS["panel"])
        style.configure("Panel2.TFrame", background=self.COLORS["panel2"])
        style.configure(
            "Title.TLabel",
            background=self.COLORS["navy"],
            foreground=self.COLORS["text"],
            font=("Segoe UI Semibold", 22),
        )
        style.configure(
            "Subtitle.TLabel",
            background=self.COLORS["navy"],
            foreground=self.COLORS["muted"],
            font=("Segoe UI", 10),
        )
        style.configure(
            "PanelTitle.TLabel",
            background=self.COLORS["panel"],
            foreground=self.COLORS["text"],
            font=("Segoe UI Semibold", 12),
        )
        style.configure(
            "Body.TLabel",
            background=self.COLORS["panel"],
            foreground=self.COLORS["text"],
        )
        style.configure(
            "Muted.TLabel",
            background=self.COLORS["panel"],
            foreground=self.COLORS["muted"],
        )
        style.configure(
            "Metric.TLabel",
            background=self.COLORS["panel2"],
            foreground=self.COLORS["text"],
            font=("Segoe UI Semibold", 20),
        )
        style.configure(
            "MetricName.TLabel",
            background=self.COLORS["panel2"],
            foreground=self.COLORS["muted"],
            font=("Segoe UI", 9),
        )
        style.configure(
            "Primary.TButton",
            background=self.COLORS["cyan"],
            foreground=self.COLORS["navy"],
            borderwidth=0,
            padding=(18, 10),
            font=("Segoe UI Semibold", 10),
        )
        style.map("Primary.TButton", background=[("active", "#64E4D1")])
        style.configure(
            "Secondary.TButton",
            background=self.COLORS["panel2"],
            foreground=self.COLORS["text"],
            borderwidth=0,
            padding=(14, 9),
        )
        style.map("Secondary.TButton", background=[("active", "#223552")])
        style.configure(
            "Danger.TButton",
            background=self.COLORS["danger"],
            foreground=self.COLORS["white"],
            borderwidth=0,
            padding=(14, 9),
            font=("Segoe UI Semibold", 10),
        )
        style.configure(
            "TNotebook",
            background=self.COLORS["navy"],
            borderwidth=0,
        )
        style.configure(
            "TNotebook.Tab",
            background=self.COLORS["navy"],
            foreground=self.COLORS["muted"],
            padding=(18, 10),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", self.COLORS["panel"])],
            foreground=[("selected", self.COLORS["text"])],
        )
        style.configure(
            "Treeview",
            background=self.COLORS["panel"],
            fieldbackground=self.COLORS["panel"],
            foreground=self.COLORS["text"],
            rowheight=30,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background=self.COLORS["panel2"],
            foreground=self.COLORS["text"],
            relief="flat",
            font=("Segoe UI Semibold", 9),
        )
        style.map("Treeview", background=[("selected", self.COLORS["blue"])])
        style.configure(
            "TEntry",
            fieldbackground=self.COLORS["panel2"],
            foreground=self.COLORS["text"],
            insertcolor=self.COLORS["text"],
            bordercolor=self.COLORS["panel2"],
            padding=8,
        )
        style.configure(
            "TCheckbutton",
            background=self.COLORS["panel"],
            foreground=self.COLORS["text"],
        )
        style.map("TCheckbutton", background=[("active", self.COLORS["panel"])])
        style.configure(
            "TSpinbox",
            fieldbackground=self.COLORS["panel2"],
            foreground=self.COLORS["text"],
            arrowcolor=self.COLORS["text"],
            padding=7,
        )

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.root, style="App.TFrame", padding=24)
        shell.pack(fill="both", expand=True)

        header = ttk.Frame(shell, style="App.TFrame")
        header.pack(fill="x", pady=(0, 16))
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="Privacy-first desktop activity intelligence for process discovery",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        self.notebook = ttk.Notebook(shell)
        self.notebook.pack(fill="both", expand=True)
        self.record_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=(0, 16, 0, 0))
        self.sessions_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=(0, 16, 0, 0))
        self.settings_tab = ttk.Frame(self.notebook, style="App.TFrame", padding=(0, 16, 0, 0))
        self.notebook.add(self.record_tab, text="Recorder")
        self.notebook.add(self.sessions_tab, text="Sessions & insights")
        self.notebook.add(self.settings_tab, text="Privacy & capture")
        self._build_record_tab()
        self._build_sessions_tab()
        self._build_settings_tab()

    def _panel(self, parent, padding=18) -> ttk.Frame:
        return ttk.Frame(parent, style="Panel.TFrame", padding=padding)

    def _build_record_tab(self) -> None:
        self.record_tab.columnconfigure(0, weight=3)
        self.record_tab.columnconfigure(1, weight=2)
        self.record_tab.rowconfigure(1, weight=1)

        metrics = ttk.Frame(self.record_tab, style="App.TFrame")
        metrics.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        for column in range(5):
            metrics.columnconfigure(column, weight=1)
        metric_data = [
            ("Elapsed", self.elapsed),
            ("Keystrokes", self.keystrokes),
            ("Mouse clicks", self.clicks),
            ("App switches", self.switches),
            ("Screenshots", self.screenshots),
        ]
        for index, (label, variable) in enumerate(metric_data):
            card = ttk.Frame(metrics, style="Panel2.TFrame", padding=15)
            card.grid(
                row=0,
                column=index,
                sticky="ew",
                padx=(0 if index == 0 else 5, 0 if index == 4 else 5),
            )
            ttk.Label(card, textvariable=variable, style="Metric.TLabel").pack(anchor="w")
            ttk.Label(card, text=label, style="MetricName.TLabel").pack(anchor="w")

        live_panel = self._panel(self.record_tab)
        live_panel.grid(row=1, column=0, sticky="nsew", padx=(0, 7))
        ttk.Label(live_panel, text="Live activity", style="PanelTitle.TLabel").pack(anchor="w")
        status_row = ttk.Frame(live_panel, style="Panel.TFrame")
        status_row.pack(fill="x", pady=(14, 18))
        self.status_indicator = ttk.Label(
            status_row,
            textvariable=self.status,
            background=self.COLORS["panel2"],
            foreground=self.COLORS["cyan"],
            padding=(10, 6),
            font=("Segoe UI Semibold", 9),
        )
        self.status_indicator.pack(side="left")
        ttk.Label(
            status_row,
            text="Counts only — typed characters are never stored",
            style="Muted.TLabel",
        ).pack(side="right")

        context_card = ttk.Frame(live_panel, style="Panel2.TFrame", padding=18)
        context_card.pack(fill="x")
        ttk.Label(
            context_card,
            text="ACTIVE APPLICATION",
            style="MetricName.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            context_card,
            textvariable=self.active_app,
            style="Metric.TLabel",
        ).pack(anchor="w", pady=(5, 6))
        ttk.Label(
            context_card,
            textvariable=self.active_title,
            style="MetricName.TLabel",
            wraplength=620,
        ).pack(anchor="w")

        ttk.Label(
            live_panel,
            text=(
                "FlowLens groups foreground-window time, interaction volume, and visual evidence "
                "into an exportable event log suitable for process analysis."
            ),
            style="Muted.TLabel",
            wraplength=650,
            justify="left",
        ).pack(anchor="w", pady=(18, 0))

        control_panel = self._panel(self.record_tab)
        control_panel.grid(row=1, column=1, sticky="nsew", padx=(7, 0))
        ttk.Label(control_panel, text="New discovery session", style="PanelTitle.TLabel").pack(
            anchor="w"
        )
        ttk.Label(control_panel, text="Session name", style="Muted.TLabel").pack(
            anchor="w", pady=(16, 5)
        )
        ttk.Entry(control_panel, textvariable=self.session_name).pack(fill="x")
        ttk.Label(control_panel, text="Participant label", style="Muted.TLabel").pack(
            anchor="w", pady=(12, 5)
        )
        ttk.Entry(control_panel, textvariable=self.user_label).pack(fill="x")
        ttk.Checkbutton(
            control_panel,
            variable=self.consent,
            text="Participant has reviewed and accepted the capture notice",
        ).pack(anchor="w", pady=(18, 14))

        button_row = ttk.Frame(control_panel, style="Panel.TFrame")
        button_row.pack(fill="x")
        self.start_button = ttk.Button(
            button_row,
            text="Start recording",
            style="Primary.TButton",
            command=self._start_recording,
        )
        self.start_button.pack(fill="x", pady=(0, 8))
        self.pause_button = ttk.Button(
            button_row,
            text="Pause",
            style="Secondary.TButton",
            command=self._toggle_pause,
            state="disabled",
        )
        self.pause_button.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.stop_button = ttk.Button(
            button_row,
            text="Stop",
            style="Danger.TButton",
            command=self._stop_recording,
            state="disabled",
        )
        self.stop_button.pack(side="left", fill="x", expand=True, padx=(4, 0))

        ttk.Separator(control_panel).pack(fill="x", pady=18)
        ttk.Label(
            control_panel,
            text=(
                "Safety defaults\n"
                "• No key values, clipboard data, or form contents\n"
                "• Recording is visible and can be paused instantly\n"
                "• Evidence remains on this computer"
            ),
            style="Muted.TLabel",
            justify="left",
        ).pack(anchor="w")

    def _build_sessions_tab(self) -> None:
        self.sessions_tab.columnconfigure(0, weight=3)
        self.sessions_tab.columnconfigure(1, weight=2)
        self.sessions_tab.rowconfigure(0, weight=1)

        list_panel = self._panel(self.sessions_tab, padding=12)
        list_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        header = ttk.Frame(list_panel, style="Panel.TFrame")
        header.pack(fill="x", padx=6, pady=(4, 10))
        ttk.Label(header, text="Recorded sessions", style="PanelTitle.TLabel").pack(side="left")
        ttk.Button(
            header,
            text="Refresh",
            style="Secondary.TButton",
            command=self._refresh_sessions,
        ).pack(side="right")

        columns = ("name", "participant", "started", "duration", "interactions")
        self.session_tree = ttk.Treeview(
            list_panel,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headings = {
            "name": ("Session", 170),
            "participant": ("Participant", 110),
            "started": ("Started", 145),
            "duration": ("Duration", 80),
            "interactions": ("Interactions", 90),
        }
        for key, (title, width) in headings.items():
            self.session_tree.heading(key, text=title)
            self.session_tree.column(key, width=width, minwidth=70)
        self.session_tree.pack(fill="both", expand=True)
        self.session_tree.bind("<<TreeviewSelect>>", self._show_session_details)

        insight_panel = self._panel(self.sessions_tab)
        insight_panel.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        ttk.Label(insight_panel, text="Session insight", style="PanelTitle.TLabel").pack(anchor="w")
        self.insight_summary = ttk.Label(
            insight_panel,
            text="Select a session to inspect application effort and export its event log.",
            style="Muted.TLabel",
            justify="left",
            wraplength=420,
        )
        self.insight_summary.pack(anchor="w", pady=(12, 14))

        self.app_tree = ttk.Treeview(
            insight_panel,
            columns=("app", "time", "keys", "clicks"),
            show="headings",
            height=10,
        )
        for key, title, width in [
            ("app", "Application", 155),
            ("time", "Time", 70),
            ("keys", "Keys", 55),
            ("clicks", "Clicks", 55),
        ]:
            self.app_tree.heading(key, text=title)
            self.app_tree.column(key, width=width)
        self.app_tree.pack(fill="both", expand=True)

        actions = ttk.Frame(insight_panel, style="Panel.TFrame")
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(
            actions,
            text="Export CSV",
            style="Primary.TButton",
            command=self._export_selected_session,
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(
            actions,
            text="Generate flow text",
            style="Primary.TButton",
            command=self._export_flow_report,
        ).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(
            actions,
            text="Open evidence",
            style="Secondary.TButton",
            command=self._open_evidence,
        ).pack(side="left", fill="x", expand=True, padx=(4, 0))

    def _build_settings_tab(self) -> None:
        panel = self._panel(self.settings_tab)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Capture policy", style="PanelTitle.TLabel").pack(anchor="w")
        ttk.Label(
            panel,
            text=(
                "Use the minimum evidence needed for the engagement. Screenshots can contain "
                "sensitive client data, so validate consent, retention, and access before recording."
            ),
            style="Muted.TLabel",
            wraplength=850,
            justify="left",
        ).pack(anchor="w", pady=(8, 18))

        ttk.Checkbutton(
            panel,
            text="Capture screenshots",
            variable=self.screenshots_enabled,
        ).pack(anchor="w", pady=5)
        ttk.Checkbutton(
            panel,
            text="Capture a screenshot when the foreground application changes",
            variable=self.screenshot_on_switch,
        ).pack(anchor="w", pady=5)
        self._setting_row(
            panel,
            "Scheduled screenshot interval (seconds; 0 disables scheduled captures)",
            self.screenshot_interval,
            0,
            3600,
        )
        self._setting_row(
            panel,
            "Activity sampling interval (seconds)",
            self.sample_interval,
            1,
            60,
        )
        self._setting_row(
            panel,
            "JPEG screenshot quality (lower values reduce storage)",
            self.screenshot_quality,
            25,
            95,
        )

        ttk.Separator(panel).pack(fill="x", pady=18)
        ttk.Label(panel, text="Data location", style="PanelTitle.TLabel").pack(anchor="w")
        ttk.Label(
            panel,
            text=str(DATA_DIR),
            style="Muted.TLabel",
            wraplength=900,
        ).pack(anchor="w", pady=(7, 0))
        ttk.Label(
            panel,
            text=(
                "Production roadmap: policy-managed exclusions, role-based access, encryption, "
                "tenant isolation, redaction, central ingestion, process clustering, and audit logs."
            ),
            style="Muted.TLabel",
            wraplength=900,
            justify="left",
        ).pack(anchor="w", pady=(22, 0))

    def _setting_row(self, parent, label: str, variable: IntVar, minimum: int, maximum: int) -> None:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=7)
        ttk.Label(row, text=label, style="Body.TLabel").pack(side="left")
        ttk.Spinbox(
            row,
            from_=minimum,
            to=maximum,
            textvariable=variable,
            width=8,
        ).pack(side="right")

    def _settings(self) -> dict:
        return {
            "screenshots_enabled": bool(self.screenshots_enabled.get()),
            "screenshot_on_switch": bool(self.screenshot_on_switch.get()),
            "screenshot_interval": int(self.screenshot_interval.get()),
            "sample_interval": int(self.sample_interval.get()),
            "screenshot_quality": int(self.screenshot_quality.get()),
            "stores_key_values": False,
            "stores_clipboard": False,
        }

    def _start_recording(self) -> None:
        if not self.consent.get():
            messagebox.showwarning(
                "Consent required",
                "Confirm that the participant has reviewed and accepted the capture notice.",
            )
            return
        name = self.session_name.get().strip()
        participant = self.user_label.get().strip()
        if not name or not participant:
            messagebox.showwarning("Missing details", "Enter a session name and participant label.")
            return
        try:
            self.recorder.start(name, participant, self._settings())
        except Exception as exc:
            messagebox.showerror("Unable to start", str(exc))
            return
        self.start_button.configure(state="disabled")
        self.pause_button.configure(state="normal", text="Pause")
        self.stop_button.configure(state="normal")
        self.notebook.select(self.record_tab)

    def _toggle_pause(self) -> None:
        paused = self.recorder.toggle_pause()
        self.pause_button.configure(text="Resume" if paused else "Pause")

    def _stop_recording(self) -> None:
        self.recorder.stop()
        self.start_button.configure(state="normal")
        self.pause_button.configure(state="disabled", text="Pause")
        self.stop_button.configure(state="disabled")
        self._refresh_sessions()

    def _process_events(self) -> None:
        try:
            while True:
                event, value = self.event_queue.get_nowait()
                if event == "status":
                    self.status.set(value)
                elif event == "metrics":
                    self.elapsed.set(format_seconds(value["duration_seconds"]))
                    self.keystrokes.set(f'{value["keystrokes"]:,}')
                    self.clicks.set(f'{value["mouse_clicks"]:,}')
                    self.switches.set(f'{value["app_switches"]:,}')
                    self.screenshots.set(f'{value["screenshots"]:,}')
                elif event == "window":
                    self.active_app.set(value.application)
                    self.active_title.set(value.title)
                elif event == "error":
                    self.status.set("Capture warning")
                elif event == "session_completed":
                    self._refresh_sessions()
        except queue.Empty:
            pass
        self.root.after(200, self._process_events)

    def _refresh_sessions(self) -> None:
        if not hasattr(self, "session_tree"):
            return
        for item in self.session_tree.get_children():
            self.session_tree.delete(item)
        for session in self.database.sessions():
            interactions = session["keystrokes"] + session["mouse_clicks"]
            started = session["started_at"].replace("T", " ")[:16]
            self.session_tree.insert(
                "",
                "end",
                iid=session["id"],
                values=(
                    session["name"],
                    session["user_label"],
                    started,
                    format_seconds(session["duration_seconds"]),
                    f"{interactions:,}",
                ),
            )

    def _show_session_details(self, _event=None) -> None:
        selection = self.session_tree.selection()
        if not selection:
            return
        self.selected_session_id = selection[0]
        session = self.database.session(self.selected_session_id)
        if not session:
            return
        for item in self.app_tree.get_children():
            self.app_tree.delete(item)
        summary = self.database.app_summary(self.selected_session_id)
        for row in summary:
            self.app_tree.insert(
                "",
                "end",
                values=(
                    row["application"],
                    format_seconds(row["duration_seconds"]),
                    f'{row["keystrokes"]:,}',
                    f'{row["mouse_clicks"]:,}',
                ),
            )
        interactions = session["keystrokes"] + session["mouse_clicks"]
        apps = len(summary)
        self.insight_summary.configure(
            text=(
                f'{session["name"]}\n'
                f'{format_seconds(session["duration_seconds"])} observed across {apps} applications · '
                f'{interactions:,} interactions · {session["screenshots"]:,} screenshots.'
            )
        )

    def _export_selected_session(self) -> None:
        if not self.selected_session_id:
            messagebox.showinfo("Select a session", "Select a session to export.")
            return
        session = self.database.session(self.selected_session_id)
        if not session:
            return
        destination = filedialog.asksaveasfilename(
            title="Export task-mining event log",
            defaultextension=".csv",
            initialfile=f'{safe_filename(session["name"])}.csv',
            filetypes=[("CSV files", "*.csv")],
        )
        if not destination:
            return
        rows = self.database.activities(self.selected_session_id)
        fields = [
            "session_id",
            "captured_at",
            "application",
            "window_title",
            "process_id",
            "duration_seconds",
            "keystrokes",
            "mouse_clicks",
            "screenshot_path",
            "event_type",
        ]
        with open(destination, "w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
        for row in rows:
            writer.writerow({field: row[field] for field in fields})
        messagebox.showinfo("Export complete", f"Event log saved to:\n{destination}")

    def _export_flow_report(self) -> None:
        if not self.selected_session_id:
            messagebox.showinfo("Select a session", "Select a session to generate a task flow.")
            return
        session = self.database.session(self.selected_session_id)
        if not session:
            return
        destination = filedialog.asksaveasfilename(
            title="Save generated task flow",
            defaultextension=".txt",
            initialfile=f'{safe_filename(session["name"])}_flow.txt',
            filetypes=[("Text files", "*.txt")],
        )
        if not destination:
            return
        rows = self.database.activities(self.selected_session_id)
        report = build_flow_report(session, rows)
        with open(destination, "w", encoding="utf-8") as handle:
            handle.write(report)
        if os.name == "nt":
            os.startfile(destination)
        messagebox.showinfo("Flow report complete", f"Generated task flow saved to:\n{destination}")

    def _open_evidence(self) -> None:
        if not self.selected_session_id:
            messagebox.showinfo("Select a session", "Select a session first.")
            return
        path = SCREENSHOT_DIR / self.selected_session_id
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(path)

    def _on_close(self) -> None:
        if self.recorder.running:
            if not messagebox.askyesno(
                "Recording active",
                "Stop the active recording and close FlowLens?",
            ):
                return
            self.recorder.stop()
        self.root.destroy()


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    root = Tk()
    TaskMiningApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
