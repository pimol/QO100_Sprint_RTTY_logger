#!/usr/bin/env python3
"""Desktop logger for the QO-100 RTTY Sprint contest.

The application stores QSOs in ADIF format and can monitor MMTTY's receive log
so that decoded tokens can be copied into the entry fields with a double-click.
"""

from __future__ import annotations

import json
import re
import sys
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

VERSION = 0.1
APP_NAME = "QO-100 RTTY Sprint Logger  (v " + str(VERSION) + ")" 
CONFIG_FILE = Path.home() / ".qo100_rtty_logger.json"
CALL_RE = re.compile(r"^[A-Z0-9/]{3,15}$")
GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}$")
MEMBER_RE = re.compile(r"^[0-9]+$")

@dataclass
class Qso:
    """Contest fields stored for a single QSO."""

    date: str
    time: str
    call: str
    rst_sent: str
    rst_rcvd: str
    grid: str
    member: str

def normalize_call(value: str) -> str:
    """Return a callsign without whitespace and in uppercase."""
    return re.sub(r"\s+", "", value).upper()

def normalize_grid(value: str) -> str:
    """Normalize a Maidenhead locator and keep its four-character square."""
    return re.sub(r"\s+", "", value).upper()[:4]

def adif_field(name: str, value: str) -> str:
    """Encode one value using ADIF's ``<NAME:LENGTH>VALUE`` syntax."""
    # The length in an ADIF field header describes the following value only.
    value = str(value)
    return f"<{name}:{len(value)}>{value}"

def qso_to_adif(qso: Qso, station_call: str, my_grid: str) -> str:
    """Serialize a QSO as one complete ADIF record."""
    fields = [
        adif_field("QSO_DATE", qso.date),
        adif_field("TIME_ON", qso.time),
        adif_field("STATION_CALLSIGN", station_call),
        adif_field("CALL", qso.call),
        adif_field("MODE", "RTTY"),
        adif_field("BAND", "13CM"),
        adif_field("SAT_NAME", "QO-100"),
        adif_field("PROP_MODE", "SAT"),
        adif_field("RST_SENT", qso.rst_sent),
        adif_field("RST_RCVD", qso.rst_rcvd),
        adif_field("GRIDSQUARE", qso.grid),
        adif_field("MY_GRIDSQUARE", my_grid),
        adif_field("COMMENT", qso.member),
        "<EOR>",
    ]
    return " ".join(fields) + "\n"

def parse_adif_records(text: str) -> list[Qso]:
    """Parse the subset of ADIF fields used by the logger."""
    # Unknown fields are intentionally ignored so logs from other programs can
    # still be opened without losing the contest data displayed by this logger.
    records: list[Qso] = []
    for raw_record in re.split(r"<EOR>", text, flags=re.IGNORECASE):
        fields: dict[str, str] = {}
        for match in re.finditer(r"<([A-Z0-9_]+):(\d+)(?::[^>]*)?>([^<]*)", raw_record, flags=re.IGNORECASE):
            name = match.group(1).upper()
            length = int(match.group(2))
            fields[name] = match.group(3)[:length].strip()
        call = normalize_call(fields.get("CALL", ""))
        if call:
            records.append(Qso(
                date=fields.get("QSO_DATE", ""),
                time=fields.get("TIME_ON", ""),
                call=call,
                rst_sent=fields.get("RST_SENT", "599"),
                rst_rcvd=fields.get("RST_RCVD", "599"),
                grid=normalize_grid(fields.get("GRIDSQUARE", "")),
                member=re.sub(r"\D", "", fields.get("COMMENT", "")),
            ))
    return records

class LoggerApp(tk.Tk):
    """Tkinter user interface and application state for the contest logger."""

    def __init__(self) -> None:
        super().__init__()
        self.title(APP_NAME)
        self.minsize(760, 520)
        self.config_data = self.load_config()
        self.station_call = tk.StringVar(value=self.config_data.get("station_call", ""))
        self.my_grid = tk.StringVar(value=self.config_data.get("my_grid", ""))
        self.log_path = Path(self.config_data.get("log_path", str(Path.home() / "Documents" / "QO100_RTTY_Sprint_2026.adi")))
        rx_value = self.config_data.get("rx_path", "")
        self.rx_path = Path(rx_value) if rx_value else None
        self.rx_position = 0
        self.rx_poll_ms = 300
        self.rx_max_lines = 12
        self.call_var = tk.StringVar()
        self.rst_sent_var = tk.StringVar(value="599")
        self.rst_rcvd_var = tk.StringVar(value="599")
        self.grid_var = tk.StringVar()
        self.member_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.clock_var = tk.StringVar()
        self.always_on_top = tk.BooleanVar(value=bool(self.config_data.get("always_on_top", True)))
        # RX target state: 0 = callsign, 1 = grid, 2 = member number. ``None``
        # means that the initial sequence is complete and no correction field
        # has been selected yet.
        self.rx_click_step: int | None = 0
        self.rx_next_var = tk.StringVar(value="Next click: Callsign")
        self.qsos: list[Qso] = []
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.call_var.trace_add("write", self.on_call_changed)
        self.grid_var.trace_add("write", self.on_grid_changed)
        self.member_var.trace_add("write", self.on_member_changed)
        self.set_always_on_top()
        self.load_existing_log()
        self.refresh_table()
        self.update_clock()
        self.initialize_rx_monitor()
        self.after(self.rx_poll_ms, self.poll_rx_file)
        if not self.valid_station_settings():
            self.after(100, self.open_settings)
        else:
            self.call_entry.focus_set()

    @staticmethod
    def load_config() -> dict:
        """Load user settings, falling back to defaults if the file is invalid."""
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def save_config(self) -> None:
        """Persist the current station, file, and window settings."""
        data = {"station_call": normalize_call(self.station_call.get()), "my_grid": normalize_grid(self.my_grid.get()), "log_path": str(self.log_path), "rx_path": str(self.rx_path) if self.rx_path else "", "always_on_top": self.always_on_top.get()}
        try:
            CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            messagebox.showwarning(APP_NAME, f"Unable to save settings:\n{exc}")

    def build_ui(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Open/create ADIF log...", command=self.choose_log)
        file_menu.add_command(label="Select MMTTY RX file...", command=self.choose_rx_file)
        file_menu.add_command(label="Station settings...", command=self.open_settings)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menu.add_cascade(label="File", menu=file_menu)
        view_menu = tk.Menu(menu, tearoff=False)
        view_menu.add_checkbutton(label="Always on top", variable=self.always_on_top, command=self.set_always_on_top)
        menu.add_cascade(label="View", menu=view_menu)
        self.config(menu=menu)

        header = ttk.Frame(self, padding=(12, 10)); header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, font=("TkDefaultFont", 14, "bold")).pack(side="left")
        ttk.Label(header, textvariable=self.clock_var, font=("TkFixedFont", 12)).pack(side="right")
        station_frame = ttk.Frame(self, padding=(12, 0, 12, 8)); station_frame.pack(fill="x")
        self.station_label = ttk.Label(station_frame); self.station_label.pack(side="left")
        self.file_label = ttk.Label(station_frame); self.file_label.pack(side="right")
        self.update_header_labels()

        form = ttk.LabelFrame(self, text="New QSO", padding=12); form.pack(fill="x", padx=12)
        for col, text in enumerate(["Callsign", "RST TX", "RST RX", "Grid (4)", "Member No."]):
            ttk.Label(form, text=text).grid(row=0, column=col, sticky="w", padx=4)
        self.call_entry = tk.Entry(form, textvariable=self.call_var, width=18, font=("TkFixedFont", 14, "bold")); self.call_entry.grid(row=1, column=0, sticky="ew", padx=4, pady=(2,4))
        self.rst_sent_entry = ttk.Entry(form, textvariable=self.rst_sent_var, width=7); self.rst_sent_entry.grid(row=1, column=1, sticky="ew", padx=4, pady=(2,4))
        self.rst_rcvd_entry = ttk.Entry(form, textvariable=self.rst_rcvd_var, width=7); self.rst_rcvd_entry.grid(row=1, column=2, sticky="ew", padx=4, pady=(2,4))
        self.grid_entry = ttk.Entry(form, textvariable=self.grid_var, width=12); self.grid_entry.grid(row=1, column=3, sticky="ew", padx=4, pady=(2,4))
        self.member_entry = ttk.Entry(form, textvariable=self.member_var, width=12); self.member_entry.grid(row=1, column=4, sticky="ew", padx=4, pady=(2,4))
        ttk.Button(form, text="LOG QSO  (Enter)", command=self.log_qso).grid(row=1, column=5, sticky="ew", padx=(12,4), pady=(2,4))
        form.columnconfigure(0, weight=2); form.columnconfigure(3, weight=1); form.columnconfigure(4, weight=1)
        self.call_entry.bind("<Return>", lambda _e: self.grid_entry.focus_set())
        self.grid_entry.bind("<Return>", lambda _e: self.member_entry.focus_set())
        self.member_entry.bind("<Return>", lambda _e: self.log_qso())
        self.rst_sent_entry.bind("<Return>", lambda _e: self.rst_rcvd_entry.focus_set())
        self.rst_rcvd_entry.bind("<Return>", lambda _e: self.grid_entry.focus_set())

        # Once the callsign -> grid -> member sequence is complete, clicking an
        # entry field selects it as the destination for the next RX token.
        self.call_entry.bind("<Button-1>", lambda _e: self.select_rx_target(0), add="+")
        self.grid_entry.bind("<Button-1>", lambda _e: self.select_rx_target(1), add="+")
        self.member_entry.bind("<Button-1>", lambda _e: self.select_rx_target(2), add="+")

        self.status_label = tk.Label(self, textvariable=self.status_var, anchor="w", padx=12, pady=8, font=("TkDefaultFont", 11, "bold")); self.status_label.pack(fill="x")

        rx_frame = ttk.LabelFrame(self, text="MMTTY receive window - double-click: Callsign -> Grid -> Member No.; then select a field to correct", padding=8)
        rx_frame.pack(fill="both", padx=12, pady=(0,8))
        rx_toolbar = ttk.Frame(rx_frame)
        rx_toolbar.pack(fill="x", pady=(0,5))
        self.rx_file_label = ttk.Label(rx_toolbar, text="RX file: not selected")
        self.rx_file_label.pack(side="left", fill="x", expand=True)
        ttk.Button(rx_toolbar, text="Choose RX file...", command=self.choose_rx_file).pack(side="right")

        target_bar = ttk.Frame(rx_frame)
        target_bar.pack(fill="x", pady=(0, 5))
        ttk.Label(target_bar, textvariable=self.rx_next_var, font=("TkDefaultFont", 10, "bold")).pack(side="left")
        ttk.Button(target_bar, text="Restart from Callsign", command=self.reset_rx_click_cycle).pack(side="right")
        self.rx_text = tk.Text(rx_frame, height=self.rx_max_lines, wrap="word", font=("TkFixedFont", 11), undo=False)
        self.rx_text.pack(fill="both", expand=True)
        self.rx_text.bind("<Double-Button-1>", self.on_rx_double_click)
        self.rx_text.tag_configure("picked", background="#fff2a8")
        table_frame = ttk.LabelFrame(self, text="Logged QSOs", padding=8); table_frame.pack(fill="both", expand=True, padx=12, pady=(0,8))
        columns = ("utc", "call", "rst", "grid", "member")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        for col, title, width in [("utc","UTC",155),("call","Callsign",150),("rst","RST TX/RX",100),("grid","Grid",100),("member","Member No.",100)]:
            self.tree.heading(col, text=title); self.tree.column(col, width=width, anchor="center")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview); self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True); scrollbar.pack(side="right", fill="y")
        footer = ttk.Frame(self, padding=(12,0,12,10)); footer.pack(fill="x")
        self.count_label = ttk.Label(footer); self.count_label.pack(side="left")
        ttk.Button(footer, text="Delete selected QSO", command=self.delete_selected).pack(side="right")

    def update_header_labels(self) -> None:
        self.station_label.config(text=f"Station: {normalize_call(self.station_call.get()) or 'NOT SET'}  |  Grid: {normalize_grid(self.my_grid.get()) or '----'}")
        self.file_label.config(text=f"Log: {self.log_path.name}")

    def update_clock(self) -> None:
        self.clock_var.set(datetime.now(timezone.utc).strftime("%Y-%m-%d  %H:%M:%S UTC")); self.after(500, self.update_clock)

    def set_always_on_top(self) -> None:
        self.attributes("-topmost", self.always_on_top.get())

    def valid_station_settings(self) -> bool:
        return bool(CALL_RE.fullmatch(normalize_call(self.station_call.get())) and GRID_RE.fullmatch(normalize_grid(self.my_grid.get())))

    def open_settings(self) -> None:
        dialog = tk.Toplevel(self); dialog.title("Station settings"); dialog.transient(self); dialog.grab_set(); dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16); frame.pack(fill="both", expand=True)
        call_var = tk.StringVar(value=normalize_call(self.station_call.get())); grid_var = tk.StringVar(value=normalize_grid(self.my_grid.get()))
        ttk.Label(frame, text="Your callsign").grid(row=0, column=0, sticky="w", pady=5)
        call_entry = ttk.Entry(frame, textvariable=call_var, width=20); call_entry.grid(row=0, column=1, sticky="ew", padx=(12,0), pady=5)
        ttk.Label(frame, text="Your grid (4 characters)").grid(row=1, column=0, sticky="w", pady=5)
        grid_entry = ttk.Entry(frame, textvariable=grid_var, width=20); grid_entry.grid(row=1, column=1, sticky="ew", padx=(12,0), pady=5)
        def save() -> None:
            call = normalize_call(call_var.get()); grid = normalize_grid(grid_var.get())
            if not CALL_RE.fullmatch(call): messagebox.showerror(APP_NAME, "Invalid callsign.", parent=dialog); call_entry.focus_set(); return
            if not GRID_RE.fullmatch(grid): messagebox.showerror(APP_NAME, "Invalid grid: enter 4 characters, for example JN45.", parent=dialog); grid_entry.focus_set(); return
            self.station_call.set(call); self.my_grid.set(grid); self.update_header_labels(); self.save_config(); dialog.destroy(); self.call_entry.focus_set()
        buttons = ttk.Frame(frame); buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(14,0))
        ttk.Button(buttons, text="Save", command=save).pack(side="right"); ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right", padx=(0,8))
        call_entry.bind("<Return>", lambda _e: grid_entry.focus_set()); grid_entry.bind("<Return>", lambda _e: save()); call_entry.focus_set()

    def choose_log(self) -> None:
        selected = filedialog.asksaveasfilename(title="Open or create an ADIF log", defaultextension=".adi", filetypes=[("ADIF files","*.adi"),("All files","*.*")], initialdir=str(self.log_path.parent), initialfile=self.log_path.name)
        if selected:
            self.log_path = Path(selected); self.load_existing_log(); self.refresh_table(); self.update_header_labels(); self.save_config(); self.call_entry.focus_set()

    def choose_rx_file(self) -> None:
        initial_dir = str(self.rx_path.parent) if self.rx_path else str(Path.home())
        selected = filedialog.askopenfilename(
            title="Select the MMTTY receive log",
            filetypes=[("Text files", "*.txt *.log"), ("All files", "*.*")],
            initialdir=initial_dir,
        )
        if not selected:
            return
        self.rx_path = Path(selected)
        self.initialize_rx_monitor()
        self.save_config()
        self.call_entry.focus_set()

    def initialize_rx_monitor(self) -> None:
        """Start monitoring at the current end of the selected MMTTY log."""
        # Clear the receive pane and ignore text already present in the log.
        # Subsequent polls display only data appended by MMTTY.
        self.rx_position = 0
        if not hasattr(self, "rx_text"):
            return
        self.rx_text.delete("1.0", "end")
        if not self.rx_path:
            self.rx_file_label.config(text="RX file: not selected")
            self.rx_text.insert("end", "Select the text file written by MMTTY.\n")
            return
        self.rx_file_label.config(text=f"RX file: {self.rx_path}")
        try:
            if not self.rx_path.exists():
                self.rx_text.insert("end", "The RX file does not exist yet; waiting for MMTTY to create it...\n")
                return
            self.rx_position = self.rx_path.stat().st_size
        except OSError as exc:
            self.rx_text.insert("end", f"Error opening the RX file: {exc}\n")

    def poll_rx_file(self) -> None:
        """Read newly appended RX bytes and schedule the next polling cycle."""
        # If MMTTY truncates or rotates the file, restart at byte zero. Binary
        # reads make the saved offset independent of text-decoding details.
        try:
            if self.rx_path and self.rx_path.exists():
                size = self.rx_path.stat().st_size
                if size < self.rx_position:
                    self.rx_position = 0
                    self.rx_text.delete("1.0", "end")
                if size > self.rx_position:
                    with self.rx_path.open("rb") as handle:
                        handle.seek(self.rx_position)
                        data = handle.read()
                        self.rx_position = handle.tell()
                    if data:
                        self.append_rx_text(data.decode("cp1252", errors="replace"))
        except OSError as exc:
            self.set_status(f"Error reading the RX file: {exc}", "error")
        finally:
            self.after(self.rx_poll_ms, self.poll_rx_file)

    def append_rx_text(self, text: str) -> None:
        """Append decoded RX text while retaining only the most recent lines."""
        if not text:
            return
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        self.rx_text.insert("end", text)
        all_text = self.rx_text.get("1.0", "end-1c")
        lines = all_text.splitlines(keepends=True)
        # Bound only the UI buffer; the source log remains untouched.
        if len(lines) > self.rx_max_lines:
            self.rx_text.delete("1.0", f"{len(lines) - self.rx_max_lines + 1}.0")
        self.rx_text.see("end")

    def on_rx_double_click(self, event) -> str:
        """Copy the double-clicked RX token to the active destination field."""
        index = self.rx_text.index(f"@{event.x},{event.y}")
        line_start = self.rx_text.index(f"{index} linestart")
        line_end = self.rx_text.index(f"{index} lineend")
        line = self.rx_text.get(line_start, line_end)
        column = int(index.split(".")[1])

        # Do not infer meaning from decoded text. Copy the token under the pointer
        # to the field selected by the current sequence or by the operator.
        token_match = None
        for match in re.finditer(r"\S+", line):
            if match.start() <= column < match.end():
                token_match = match
                break
        if token_match is None:
            return "break"

        token = token_match.group(0).strip(" \t\r\n,.;:!?()[]{}<>\"'")
        if not token:
            return "break"

        if self.rx_click_step is None:
            self.set_status(
                "First select the Callsign, Grid, or Member No. field to correct.",
                "error",
            )
            self.bell()
            return "break"

        token_start = f"{line_start}+{token_match.start()}c"
        token_end = f"{line_start}+{token_match.end()}c"
        self.rx_text.tag_remove("picked", "1.0", "end")
        self.rx_text.tag_add("picked", token_start, token_end)

        target = self.rx_click_step
        if target == 0:
            self.call_var.set(token)
            self.set_status(f"Copied to Callsign: {token}", "new")
        elif target == 1:
            self.grid_var.set(token)
            self.set_status(f"Copied to Grid: {token}", "new")
        else:
            self.member_var.set(token)
            self.set_status(f"Copied to Member No.: {token}", "new")

        # The initial sequence advances through callsign, grid, and member number.
        # A correction fills its selected field and then returns to the idle state.
        if target < 2:
            self.rx_click_step = target + 1
            next_labels = ("Callsign", "Grid", "Member No.")
            self.rx_next_var.set(f"Next click: {next_labels[self.rx_click_step]}")
        else:
            self.rx_click_step = None
            self.rx_next_var.set("Corrections: first click the field to edit")

        return "break"

    def select_rx_target(self, target: int) -> None:
        """Select a correction target after the initial RX sequence is complete."""
        if self.rx_click_step is not None:
            return

        labels = ("Callsign", "Grid", "Member No.")
        self.rx_click_step = target
        self.rx_next_var.set(f"Selected correction field: {labels[target]}")
        self.set_status(
            f"Now double-click the RX text to copy into {labels[target]}, "
            "or edit the field manually.",
            "normal",
        )

    def reset_rx_click_cycle(self) -> None:
        """Restart the normal callsign -> grid -> member RX copy sequence."""
        self.rx_click_step = 0
        self.rx_next_var.set("Next click: Callsign")
        self.set_status("Click sequence reset: the next token goes to Callsign.", "normal")

    def ensure_log_file(self) -> None:
        """Create the log and its minimal ADIF header when necessary."""
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.log_path.exists() or self.log_path.stat().st_size == 0:
            header = adif_field("ADIF_VER", "3.1.4") + " " + adif_field("PROGRAMID", "QO100_RTTY_LOGGER") + " " + adif_field("PROGRAMVERSION", "1.0") + " <EOH>\n"
            self.log_path.write_text(header, encoding="utf-8")

    def load_existing_log(self) -> None:
        try:
            self.qsos = parse_adif_records(self.log_path.read_text(encoding="utf-8", errors="replace")) if self.log_path.exists() else []
            self.set_status(f"Ready: {len(self.qsos)} QSOs loaded.", "normal")
        except OSError as exc:
            self.qsos = []; messagebox.showerror(APP_NAME, f"Error reading the log:\n{exc}")

    def on_call_changed(self, *_args) -> None:
        current = self.call_var.get(); normalized = normalize_call(current)
        if current != normalized: self.call_var.set(normalized); return
        self.update_duplicate_status()

    def on_grid_changed(self, *_args) -> None:
        current = self.grid_var.get(); normalized = normalize_grid(current)
        if current != normalized: self.grid_var.set(normalized)

    def on_member_changed(self, *_args) -> None:
        current = self.member_var.get(); digits = re.sub(r"\D", "", current)
        if current != digits: self.member_var.set(digits)

    def update_duplicate_status(self) -> bool:
        """Update duplicate feedback and return whether the callsign exists."""
        # Contest rules allow only one QSO per callsign; any earlier match is a
        # duplicate regardless of its other fields.
        call = normalize_call(self.call_var.get()); duplicates = [q for q in self.qsos if q.call == call] if call else []
        if duplicates:
            q = duplicates[-1]; self.set_status(f"DUPLICATE: {call} was already worked at {q.time[:2]}:{q.time[2:4]}:{q.time[4:6]} UTC.", "duplicate"); self.call_entry.config(bg="#ffb3b3"); self.bell(); return True
        self.call_entry.config(bg="white")
        self.set_status(f"NEW: {call} is not in the log." if call else f"Ready: {len(self.qsos)} QSOs in the log.", "new" if call else "normal"); return False

    def set_status(self, text: str, kind: str) -> None:
        self.status_var.set(text); colors = {"normal":("SystemButtonFace","black"),"new":("#d8f5d0","#174d12"),"duplicate":("#ffb3b3","#700000"),"saved":("#cde8ff","#003c70"),"error":("#ffd6a5","#6b3300")}; bg, fg = colors.get(kind, colors["normal"]); self.status_label.config(bg=bg, fg=fg)

    def validate_qso(self) -> tuple[bool, str]:
        """Validate the entry form and return an error suitable for the UI."""
        call = normalize_call(self.call_var.get()); rst_sent = self.rst_sent_var.get().strip(); rst_rcvd = self.rst_rcvd_var.get().strip(); grid = normalize_grid(self.grid_var.get()); member = self.member_var.get().strip()
        if not CALL_RE.fullmatch(call): return False, "Missing or invalid callsign."
        if any(q.call == call for q in self.qsos): return False, f"{call} is already in the log."
        if not re.fullmatch(r"\d{2,3}", rst_sent): return False, "Invalid TX RST."
        if not re.fullmatch(r"\d{2,3}", rst_rcvd): return False, "Invalid RX RST."
        if not GRID_RE.fullmatch(grid): return False, "Invalid grid: use 4 characters, for example JN45."
        if not MEMBER_RE.fullmatch(member): return False, "The member number must contain digits only."
        return True, ""

    def log_qso(self) -> None:
        if not self.valid_station_settings(): self.open_settings(); return
        valid, error = self.validate_qso()
        if not valid: self.set_status(error, "error"); self.bell(); return
        now = datetime.now(timezone.utc)
        qso = Qso(now.strftime("%Y%m%d"), now.strftime("%H%M%S"), normalize_call(self.call_var.get()), self.rst_sent_var.get().strip(), self.rst_rcvd_var.get().strip(), normalize_grid(self.grid_var.get()), self.member_var.get().strip())
        try:
            self.ensure_log_file()
            with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(qso_to_adif(qso, normalize_call(self.station_call.get()), normalize_grid(self.my_grid.get()))); handle.flush()
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"QSO not saved:\n{exc}"); return
        self.qsos.append(qso); self.refresh_table(); self.set_status(f"SAVED: {qso.call}  {qso.grid}  member {qso.member}", "saved"); self.clear_entry_fields()

    def clear_entry_fields(self) -> None:
        self.call_var.set("")
        self.grid_var.set("")
        self.member_var.set("")
        self.rst_sent_var.set("599")
        self.rst_rcvd_var.set("599")
        self.rx_click_step = 0
        self.rx_next_var.set("Next click: Callsign")
        self.call_entry.focus_set()

    def refresh_table(self) -> None:
        for item in self.tree.get_children(): self.tree.delete(item)
        for rev_index, qso in enumerate(reversed(self.qsos)):
            date_display = f"{qso.date[0:4]}-{qso.date[4:6]}-{qso.date[6:8]}" if len(qso.date)==8 else qso.date
            time_display = f"{qso.time[0:2]}:{qso.time[2:4]}:{qso.time[4:6]}" if len(qso.time)>=6 else qso.time
            original_index = len(self.qsos)-1-rev_index
            self.tree.insert("", "end", iid=str(original_index), values=(f"{date_display} {time_display}", qso.call, f"{qso.rst_sent}/{qso.rst_rcvd}", qso.grid, qso.member))
        self.count_label.config(text=f"QSOs: {len(self.qsos)}  |  Unique callsigns: {len({q.call for q in self.qsos})}")

    def rewrite_log(self) -> None:
        """Rewrite the complete ADIF log after an in-memory record is removed."""
        # Build the replacement beside the log, then rename it over the original
        # so readers never observe a partially written ADIF file.
        self.ensure_log_file(); temp_path = self.log_path.with_suffix(self.log_path.suffix + ".tmp")
        header = adif_field("ADIF_VER", "3.1.4") + " " + adif_field("PROGRAMID", "QO100_RTTY_LOGGER") + " " + adif_field("PROGRAMVERSION", "1.0") + " <EOH>\n"
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(header)
            for qso in self.qsos: handle.write(qso_to_adif(qso, normalize_call(self.station_call.get()), normalize_grid(self.my_grid.get())))
        temp_path.replace(self.log_path)

    def delete_selected(self) -> None:
        selected = self.tree.selection()
        if not selected: messagebox.showinfo(APP_NAME, "Select a QSO first."); return
        index = int(selected[0]); qso = self.qsos[index]
        if not messagebox.askyesno(APP_NAME, f"Delete the QSO with {qso.call}?\nThe ADIF file will be rewritten."): return
        removed = self.qsos.pop(index)
        try: self.rewrite_log()
        except OSError as exc: self.qsos.insert(index, removed); messagebox.showerror(APP_NAME, f"Unable to rewrite the log:\n{exc}"); return
        self.refresh_table(); self.set_status(f"QSO with {removed.call} deleted.", "normal"); self.call_entry.focus_set()

    def on_close(self) -> None:
        self.save_config(); self.destroy()

def main() -> int:
    try:
        app = LoggerApp(); app.mainloop(); return 0
    except tk.TclError as exc:
        print(f"Tkinter GUI error: {exc}", file=sys.stderr); return 1

if __name__ == "__main__":
    raise SystemExit(main())