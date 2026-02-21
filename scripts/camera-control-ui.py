#!/usr/bin/env python3

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shlex
import subprocess
from typing import Any
from urllib.parse import parse_qs, urlparse


CONTROL_LINE_RE = re.compile(
    r"^\s*([a-zA-Z0-9_]+)\s+0x[0-9a-fA-F]+\s+\(([^)]+)\)\s*:\s*(.*)$"
)
MENU_LINE_RE = re.compile(r"^\s*([0-9]+):\s*(.+)$")
SAFE_CONTROL_RE = re.compile(r"^[a-zA-Z0-9_]+$")
SAFE_ENV_VALUE_RE = re.compile(r"^[A-Za-z0-9_./:-]+$")
ISO_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T[0-9:+-]+)")
SENDER_METRIC_RE = re.compile(
    r"Sending (?P<fps>[0-9]+(?:\.[0-9]+)?) fps"
    r"(?: \(connections=(?P<connections>-?\d+)\))? \| "
    r"capture->send age ms min=(?P<age_min>[0-9.]+) avg=(?P<age_avg>[0-9.]+) max=(?P<age_max>[0-9.]+) \| "
    r"step ms appsink_wait avg=(?P<appsink_wait>[0-9.]+) map_copy avg=(?P<map_copy>[0-9.]+) "
    r"ndi_send avg=(?P<ndi_send>[0-9.]+) frame_proc avg=(?P<frame_proc>[0-9.]+)"
    r"(?: \| stale_drop=(?P<stale_drop_num>\d+)\/(?P<stale_drop_den>\d+) "
    r"\((?P<stale_drop_pct>[0-9.]+)%\) threshold=(?P<stale_threshold_ms>[0-9.]+)ms)?"
)


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HDMIStreamer Camera Controls</title>
  <style>
    :root {
      --bg: #0d1117;
      --panel: #161b22;
      --text: #e6edf3;
      --muted: #8b949e;
      --accent: #2f81f7;
      --ok: #238636;
      --warn: #d29922;
      --err: #da3633;
      --border: #30363d;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
      background: radial-gradient(1200px 600px at -10% -10%, #1f2937, var(--bg));
      color: var(--text);
    }
    .wrap {
      max-width: 1100px;
      margin: 0 auto;
      padding: 20px;
    }
    .card {
      background: color-mix(in oklab, var(--panel) 92%, black);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px;
      margin-bottom: 12px;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 24px;
      font-weight: 700;
      letter-spacing: 0.2px;
    }
    .meta {
      color: var(--muted);
      font-size: 13px;
    }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-top: 10px;
    }
    button {
      border: 1px solid var(--border);
      background: #21262d;
      color: var(--text);
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 13px;
      cursor: pointer;
    }
    button.primary { background: var(--accent); border-color: var(--accent); color: white; }
    button.warn { background: color-mix(in oklab, var(--warn) 65%, black); border-color: var(--warn); color: #111; }
    button.ok { background: var(--ok); border-color: var(--ok); color: white; }
    input[type="text"], input[type="number"], select {
      width: 100%;
      background: #0d1117;
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 6px 8px;
      font-size: 13px;
    }
    .status {
      margin-top: 10px;
      font-size: 13px;
      color: var(--muted);
      min-height: 20px;
    }
    .status.ok { color: #3fb950; }
    .status.err { color: #ff7b72; }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    thead th {
      text-align: left;
      color: var(--muted);
      font-weight: 600;
      padding: 8px 6px;
      border-bottom: 1px solid var(--border);
    }
    tbody td {
      padding: 8px 6px;
      border-bottom: 1px solid #20262e;
      vertical-align: middle;
    }
    .mono { font-family: "IBM Plex Mono", ui-monospace, monospace; }
    .control-stack {
      display: grid;
      grid-template-columns: 1fr 96px;
      gap: 8px;
      align-items: center;
    }
    .control-stack input[type="range"] {
      width: 100%;
    }
    .badge {
      display: inline-block;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 2px 8px;
      margin-right: 4px;
      font-size: 11px;
      color: var(--muted);
    }
    @media (max-width: 760px) {
      table, thead, tbody, th, td, tr { display: block; }
      thead { display: none; }
      tbody td { border-bottom: none; padding: 6px 0; }
      tbody tr { border-bottom: 1px solid var(--border); padding: 10px 0; }
      tbody td::before {
        content: attr(data-label);
        display: block;
        color: var(--muted);
        font-size: 11px;
        margin-bottom: 3px;
      }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Camera Controls</h1>
      <div class="meta" id="meta">Loading...</div>
      <div class="toolbar">
        <button id="refreshBtn">Refresh</button>
        <button id="applyBtn" class="primary">Apply All</button>
        <button id="manualBtn" class="ok">Preset: Manual</button>
        <button id="autoBtn">Preset: Auto</button>
        <button id="persistBtn">Persist Current</button>
        <button id="restartBtn" class="warn">Restart NDI Sender</button>
        <label class="meta"><input id="autoApplyToggle" type="checkbox" checked> auto-apply on change</label>
        <label class="meta"><input id="persistToggle" type="checkbox" checked> persist on change/apply/preset</label>
      </div>
      <div class="toolbar">
        <label class="meta">API token (optional):</label>
        <input id="tokenInput" type="text" placeholder="X-Auth-Token" style="max-width:260px">
        <button id="saveTokenBtn">Use Token</button>
      </div>
      <div id="status" class="status"></div>
    </div>

    <div class="card">
      <div class="meta">Sender latency telemetry (sender-side, not full glass-to-glass)</div>
      <div class="toolbar">
        <span class="badge">sender <span id="latSenderState">-</span></span>
        <span class="badge">fps <span id="latFps">-</span></span>
        <span class="badge">connections <span id="latConnections">-</span></span>
        <span class="badge">age avg ms <span id="latAgeAvg">-</span></span>
        <span class="badge">age max ms <span id="latAgeMax">-</span></span>
        <span class="badge">ndi send ms <span id="latNdiSend">-</span></span>
        <span class="badge">stale drop % <span id="latStalePct">-</span></span>
      </div>
      <div class="meta" id="latTs">last sample: -</div>
    </div>

    <div class="card">
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Type</th>
            <th>Current</th>
            <th>Control</th>
            <th>Meta</th>
          </tr>
        </thead>
        <tbody id="controlsBody"></tbody>
      </table>
    </div>
  </div>

  <script>
    const state = {
      controls: [],
      token: localStorage.getItem("hmdi_ui_token") || "",
      config: null,
    };

    const el = {
      meta: document.getElementById("meta"),
      body: document.getElementById("controlsBody"),
      status: document.getElementById("status"),
      autoApplyToggle: document.getElementById("autoApplyToggle"),
      persistToggle: document.getElementById("persistToggle"),
      tokenInput: document.getElementById("tokenInput"),
      latSenderState: document.getElementById("latSenderState"),
      latFps: document.getElementById("latFps"),
      latConnections: document.getElementById("latConnections"),
      latAgeAvg: document.getElementById("latAgeAvg"),
      latAgeMax: document.getElementById("latAgeMax"),
      latNdiSend: document.getElementById("latNdiSend"),
      latStalePct: document.getElementById("latStalePct"),
      latTs: document.getElementById("latTs"),
    };

    function setStatus(msg, ok = true) {
      el.status.textContent = msg;
      el.status.className = "status " + (ok ? "ok" : "err");
    }

    async function api(path, opts = {}) {
      const headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
      if (state.token) {
        headers["X-Auth-Token"] = state.token;
      }
      const res = await fetch(path, Object.assign({}, opts, { headers }));
      const text = await res.text();
      let data = {};
      try { data = text ? JSON.parse(text) : {}; } catch (_) {}
      if (!res.ok) {
        throw new Error(data.error || res.status + " " + res.statusText);
      }
      return data;
    }

    function valueCell(control) {
      const id = "ctrl-" + control.name;
      if (control.read_only) {
        return '<span class="mono">' + control.value + '</span>';
      }
      if (control.type === "bool") {
        const checked = Number(control.value) ? "checked" : "";
        return '<label><input type="checkbox" id="' + id + '" ' + checked + '> enabled</label>';
      }
      if (control.type === "menu") {
        const opts = (control.menu || []).map(opt => {
          const selected = Number(opt.value) === Number(control.value) ? "selected" : "";
          return '<option value="' + opt.value + '" ' + selected + '>' + opt.value + ' - ' + opt.label + '</option>';
        }).join("");
        return '<select id="' + id + '">' + opts + '</select>';
      }
      const min = control.min !== null ? 'min="' + control.min + '"' : "";
      const max = control.max !== null ? 'max="' + control.max + '"' : "";
      const step = control.step !== null ? 'step="' + control.step + '"' : 'step="1"';
      const value = control.value !== null ? control.value : "";
      if ((control.type === "int" || control.type === "int64") && control.min !== null && control.max !== null) {
        const rid = "ctrl-range-" + control.name;
        return `
          <div class="control-stack">
            <input id="${rid}" type="range" ${min} ${max} ${step} value="${value}">
            <input id="${id}" type="number" ${min} ${max} ${step} value="${value}">
          </div>
        `;
      }
      return '<input id="' + id + '" type="number" ' + min + ' ' + max + ' ' + step + ' value="' + value + '">';
    }

    function renderControls() {
      const rows = state.controls.map(control => {
        const flags = (control.flags || []).map(f => '<span class="badge">' + f + '</span>').join("");
        const meta = [
          control.min !== null ? "min=" + control.min : "",
          control.max !== null ? "max=" + control.max : "",
          control.step !== null ? "step=" + control.step : "",
          control.default !== null ? "default=" + control.default : "",
          flags
        ].filter(Boolean).join(" ");
        const current = control.value_label ? (control.value + " (" + control.value_label + ")") : control.value;
        return `
          <tr data-name="${control.name}">
            <td data-label="Name" class="mono">${control.name}</td>
            <td data-label="Type">${control.type}${control.read_only ? " (read-only)" : ""}</td>
            <td data-label="Current" class="mono">${current}</td>
            <td data-label="Control">${valueCell(control)}</td>
            <td data-label="Meta">${meta || "-"}</td>
          </tr>
        `;
      }).join("");
      el.body.innerHTML = rows || '<tr><td colspan="5">No controls detected</td></tr>';
      attachControlListeners();
    }

    function controlInputValue(control) {
      const input = document.getElementById("ctrl-" + control.name);
      if (!input) {
        return null;
      }
      if (control.type === "bool") {
        return input.checked ? 1 : 0;
      }
      const raw = input.value;
      if (raw === "") {
        return null;
      }
      return Number.isNaN(Number(raw)) ? raw : Number(raw);
    }

    async function applySingleControl(control) {
      const value = controlInputValue(control);
      if (value === null) {
        return;
      }
      const persist = el.persistToggle.checked;
      const payload = { values: { [control.name]: value }, persist };
      const data = await api("/api/apply", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const failed = Object.keys(data.failed || {}).length;
      if (failed) {
        setStatus(`Failed to apply ${control.name}`, false);
      } else {
        setStatus(`Applied ${control.name}=${value}`, true);
      }
      await refreshAll();
    }

    function attachControlListeners() {
      for (const control of state.controls) {
        if (control.read_only || control.inactive) {
          continue;
        }
        const input = document.getElementById("ctrl-" + control.name);
        if (!input) {
          continue;
        }
        const rangeInput = document.getElementById("ctrl-range-" + control.name);

        if (rangeInput) {
          rangeInput.addEventListener("input", () => {
            input.value = rangeInput.value;
          });
          rangeInput.addEventListener("change", () => {
            input.value = rangeInput.value;
            if (!el.autoApplyToggle.checked) {
              return;
            }
            applySingleControl(control).catch(err => setStatus(err.message, false));
          });
          input.addEventListener("input", () => {
            if (input.value === "") {
              return;
            }
            rangeInput.value = input.value;
          });
        }

        input.addEventListener("change", () => {
          if (!el.autoApplyToggle.checked) {
            return;
          }
          applySingleControl(control).catch(err => setStatus(err.message, false));
        });
        if (input.tagName === "INPUT" && input.type === "number") {
          input.addEventListener("keydown", (ev) => {
            if (ev.key === "Enter") {
              ev.preventDefault();
              input.blur();
            }
          });
        }
      }
    }

    function collectValues() {
      const values = {};
      for (const control of state.controls) {
        if (control.read_only || control.inactive) {
          continue;
        }
        const id = "ctrl-" + control.name;
        const input = document.getElementById(id);
        if (!input) {
          continue;
        }
        if (control.type === "bool") {
          values[control.name] = input.checked ? 1 : 0;
        } else {
          const val = input.value;
          if (val === "") {
            continue;
          }
          values[control.name] = Number.isNaN(Number(val)) ? val : Number(val);
        }
      }
      return values;
    }

    async function refreshLatency() {
      const data = await api("/api/latency");
      const metrics = data.metrics || null;
      el.latSenderState.textContent = data.sender_state || "-";
      if (!metrics) {
        el.latFps.textContent = "-";
        el.latConnections.textContent = "-";
        el.latAgeAvg.textContent = "-";
        el.latAgeMax.textContent = "-";
        el.latNdiSend.textContent = "-";
        el.latStalePct.textContent = "-";
        el.latTs.textContent = "last sample: n/a";
        return;
      }
      el.latFps.textContent = Number(metrics.fps || 0).toFixed(1);
      el.latConnections.textContent = String(metrics.connections ?? "-");
      el.latAgeAvg.textContent = Number(metrics.age_avg_ms || 0).toFixed(2);
      el.latAgeMax.textContent = Number(metrics.age_max_ms || 0).toFixed(2);
      el.latNdiSend.textContent = Number(metrics.ndi_send_ms || 0).toFixed(2);
      el.latStalePct.textContent = Number(metrics.stale_drop_pct || 0).toFixed(2);
      el.latTs.textContent = "last sample: " + (metrics.timestamp || "unknown");
    }

    async function refreshAll() {
      const config = await api("/api/config");
      state.config = config;
      el.meta.textContent = `device=${config.device} | sender=${config.sender_service} (${config.sender_state}) | token_required=${config.token_required}`;
      const data = await api("/api/controls");
      state.controls = data.controls || [];
      renderControls();
      await refreshLatency();
      setStatus("Loaded controls", true);
    }

    async function applyAll() {
      const values = collectValues();
      const persist = el.persistToggle.checked;
      const data = await api("/api/apply", {
        method: "POST",
        body: JSON.stringify({ values, persist }),
      });
      const failed = Object.keys(data.failed || {}).length;
      if (failed) {
        setStatus(`Applied with ${failed} failures`, false);
      } else {
        setStatus(`Applied ${Object.keys(data.applied || {}).length} controls`, true);
      }
      await refreshAll();
    }

    async function applyPreset(name) {
      const persist = el.persistToggle.checked;
      const data = await api("/api/preset", {
        method: "POST",
        body: JSON.stringify({ name, persist }),
      });
      const failed = Object.keys(data.failed || {}).length;
      if (failed) {
        setStatus(`Preset ${name}: ${failed} failures`, false);
      } else {
        setStatus(`Preset ${name} applied`, true);
      }
      await refreshAll();
    }

    async function persistCurrent() {
      await api("/api/persist-current", { method: "POST", body: "{}" });
      setStatus("Persisted current controls to startup defaults", true);
    }

    async function restartSender() {
      const data = await api("/api/restart-sender", { method: "POST", body: "{}" });
      setStatus(`Sender restart requested; state=${data.sender_state}`, true);
      await refreshAll();
    }

    document.getElementById("refreshBtn").onclick = () => refreshAll().catch(err => setStatus(err.message, false));
    document.getElementById("applyBtn").onclick = () => applyAll().catch(err => setStatus(err.message, false));
    document.getElementById("manualBtn").onclick = () => applyPreset("manual").catch(err => setStatus(err.message, false));
    document.getElementById("autoBtn").onclick = () => applyPreset("auto").catch(err => setStatus(err.message, false));
    document.getElementById("persistBtn").onclick = () => persistCurrent().catch(err => setStatus(err.message, false));
    document.getElementById("restartBtn").onclick = () => restartSender().catch(err => setStatus(err.message, false));
    document.getElementById("saveTokenBtn").onclick = () => {
      state.token = el.tokenInput.value.trim();
      localStorage.setItem("hmdi_ui_token", state.token);
      refreshAll().catch(err => setStatus(err.message, false));
    };

    el.tokenInput.value = state.token;
    setInterval(() => {
      refreshLatency().catch(() => {});
    }, 2000);
    refreshAll().catch(err => setStatus(err.message, false));
  </script>
</body>
</html>
"""


class CameraControlError(RuntimeError):
    pass


@dataclass
class UIServerConfig:
    device: str
    env_file: Path
    sender_service: str
    auth_token: str


class CameraControlHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], cfg: UIServerConfig) -> None:
        super().__init__(server_address, CameraControlHandler)
        self.cfg = cfg


def run_command(command: list[str]) -> str:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CameraControlError(
            f"Command failed ({result.returncode}): {' '.join(command)}\n{result.stderr.strip()}"
        )
    return result.stdout


def parse_int_field(meta: str, key: str) -> int | None:
    match = re.search(rf"\b{key}=(-?\d+)", meta)
    if not match:
        return None
    return int(match.group(1))


def parse_flags(meta: str) -> list[str]:
    match = re.search(r"\bflags=([^\s]+)", meta)
    if not match:
        return []
    return [flag.strip() for flag in match.group(1).split(",") if flag.strip()]


def parse_value(meta: str) -> tuple[int | str | None, str | None]:
    match = re.search(r"\bvalue=([^\s]+)(?:\s+\(([^)]+)\))?", meta)
    if not match:
        return (None, None)
    raw = match.group(1)
    label = match.group(2)
    try:
        return (int(raw), label)
    except ValueError:
        return (raw, label)


def parse_controls(raw_text: str) -> list[dict[str, Any]]:
    controls: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in raw_text.splitlines():
        control_match = CONTROL_LINE_RE.match(line)
        if control_match:
            if current is not None:
                controls.append(current)

            name, control_type, meta = control_match.groups()
            value, value_label = parse_value(meta)
            flags = parse_flags(meta)
            current = {
                "name": name,
                "type": control_type.strip().lower(),
                "min": parse_int_field(meta, "min"),
                "max": parse_int_field(meta, "max"),
                "step": parse_int_field(meta, "step"),
                "default": parse_int_field(meta, "default"),
                "value": value,
                "value_label": value_label,
                "flags": flags,
                "inactive": "inactive" in flags,
                "read_only": "read-only" in flags,
                "menu": [],
            }
            continue

        menu_match = MENU_LINE_RE.match(line)
        if menu_match and current is not None:
            option_value = int(menu_match.group(1))
            option_label = menu_match.group(2).strip()
            current["menu"].append({"value": option_value, "label": option_label})

    if current is not None:
        controls.append(current)

    return controls


def get_controls(device: str) -> list[dict[str, Any]]:
    text = run_command(["v4l2-ctl", "-d", device, "--list-ctrls-menus"])
    return parse_controls(text)


def control_map(controls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {ctrl["name"]: ctrl for ctrl in controls}


def coerce_control_value(raw: Any, ctrl: dict[str, Any]) -> int | str:
    ctrl_type = ctrl["type"]
    if ctrl_type == "bool":
        if isinstance(raw, bool):
            value = 1 if raw else 0
        elif isinstance(raw, (int, float)):
            value = 1 if int(raw) != 0 else 0
        else:
            normalized = str(raw).strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                value = 1
            elif normalized in {"0", "false", "no", "off"}:
                value = 0
            else:
                raise CameraControlError(f"Invalid bool value for {ctrl['name']}: {raw}")
    elif ctrl_type in {"int", "int64", "menu"}:
        value = int(raw)
    else:
        value = str(raw)

    minimum = ctrl.get("min")
    maximum = ctrl.get("max")
    if isinstance(value, int):
        if minimum is not None and value < minimum:
            raise CameraControlError(f"{ctrl['name']} below min ({value} < {minimum})")
        if maximum is not None and value > maximum:
            raise CameraControlError(f"{ctrl['name']} above max ({value} > {maximum})")
    return value


def apply_control_values(
    device: str,
    controls: list[dict[str, Any]],
    values: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    known = control_map(controls)
    applied: dict[str, Any] = {}
    failed: dict[str, str] = {}

    for name, raw_value in values.items():
        if not SAFE_CONTROL_RE.match(name):
            failed[name] = "invalid control name"
            continue
        if name not in known:
            failed[name] = "unknown control"
            continue
        ctrl = known[name]
        if ctrl.get("read_only"):
            failed[name] = "read-only control"
            continue

        try:
            value = coerce_control_value(raw_value, ctrl)
            run_command(["v4l2-ctl", "-d", device, "--set-ctrl", f"{name}={value}"])
            applied[name] = value
        except Exception as exc:  # noqa: BLE001
            failed[name] = str(exc)

    return (applied, failed)


def format_env_value(value: str) -> str:
    if SAFE_ENV_VALUE_RE.match(value):
        return value
    return shlex.quote(value)


def upsert_env_key(path: Path, key: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        lines = []

    rendered = f"{key}={format_env_value(value)}"
    out_lines: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(f"{key}="):
            if not replaced:
                out_lines.append(rendered)
                replaced = True
            continue
        out_lines.append(line)
    if not replaced:
        out_lines.append(rendered)

    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


def persist_controls(env_file: Path, values: dict[str, Any]) -> str:
    spec = ",".join(f"{name}={values[name]}" for name in sorted(values))
    upsert_env_key(env_file, "HMDI_USB_APPLY_CONTROLS", "1")
    upsert_env_key(env_file, "HMDI_USB_CONTROL_PRESET", "manual")
    upsert_env_key(env_file, "HMDI_USB_SET_CTRLS", spec)
    return spec


def manual_preset_values() -> dict[str, Any]:
    return {
        "auto_exposure": int(os.getenv("HMDI_USB_AUTO_EXPOSURE", "1")),
        "exposure_time_absolute": int(
            os.getenv(
                "HMDI_USB_EXPOSURE_TIME_ABSOLUTE",
                os.getenv("HMDI_USB_EXPOSURE_ABSOLUTE", "157"),
            )
        ),
        "exposure_dynamic_framerate": int(
            os.getenv("HMDI_USB_EXPOSURE_DYNAMIC_FRAMERATE", "0")
        ),
        "white_balance_automatic": int(
            os.getenv("HMDI_USB_WHITE_BALANCE_AUTOMATIC", "0")
        ),
        "white_balance_temperature": int(
            os.getenv("HMDI_USB_WHITE_BALANCE_TEMPERATURE", "4600")
        ),
        "power_line_frequency": int(os.getenv("HMDI_USB_POWER_LINE_FREQUENCY", "2")),
        "gain": int(os.getenv("HMDI_USB_GAIN", "0")),
    }


def auto_preset_values() -> dict[str, Any]:
    return {
        "auto_exposure": int(os.getenv("HMDI_USB_AUTO_EXPOSURE", "3")),
        "exposure_dynamic_framerate": int(
            os.getenv("HMDI_USB_EXPOSURE_DYNAMIC_FRAMERATE", "1")
        ),
        "white_balance_automatic": int(
            os.getenv("HMDI_USB_WHITE_BALANCE_AUTOMATIC", "1")
        ),
        "power_line_frequency": int(os.getenv("HMDI_USB_POWER_LINE_FREQUENCY", "2")),
    }


def sender_state(service_name: str) -> str:
    result = subprocess.run(
        ["systemctl", "is-active", service_name],
        check=False,
        capture_output=True,
        text=True,
    )
    state = result.stdout.strip()
    if state:
        return state
    return "unknown"


def _to_int(raw: str | None, fallback: int = 0) -> int:
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def _to_float(raw: str | None, fallback: float = 0.0) -> float:
    if raw is None:
        return fallback
    try:
        return float(raw)
    except ValueError:
        return fallback


def latest_sender_metrics(service_name: str) -> dict[str, Any] | None:
    try:
        text = run_command(["journalctl", "-u", service_name, "-n", "120", "--no-pager"])
    except Exception:  # noqa: BLE001
        return None

    lines = text.splitlines()
    for line in reversed(lines):
        match = SENDER_METRIC_RE.search(line)
        if match is None:
            continue

        ts_match = ISO_TS_RE.search(line)
        return {
            "timestamp": ts_match.group(1) if ts_match else None,
            "fps": _to_float(match.group("fps")),
            "connections": _to_int(match.group("connections"), -1),
            "age_min_ms": _to_float(match.group("age_min")),
            "age_avg_ms": _to_float(match.group("age_avg")),
            "age_max_ms": _to_float(match.group("age_max")),
            "appsink_wait_ms": _to_float(match.group("appsink_wait")),
            "map_copy_ms": _to_float(match.group("map_copy")),
            "ndi_send_ms": _to_float(match.group("ndi_send")),
            "frame_proc_ms": _to_float(match.group("frame_proc")),
            "stale_drop_num": _to_int(match.group("stale_drop_num"), 0),
            "stale_drop_den": _to_int(match.group("stale_drop_den"), 0),
            "stale_drop_pct": _to_float(match.group("stale_drop_pct"), 0.0),
            "stale_threshold_ms": _to_float(match.group("stale_threshold_ms"), 0.0),
            "raw": line.strip(),
        }
    return None


class CameraControlHandler(BaseHTTPRequestHandler):
    server: CameraControlHTTPServer

    def log_message(self, fmt: str, *args: Any) -> None:
        logging.info("%s - %s", self.address_string(), fmt % args)

    def send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(HTTPStatus.OK.value)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        if not data:
            return {}
        try:
            parsed = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise CameraControlError(f"Invalid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise CameraControlError("JSON body must be an object")
        return parsed

    def is_authorized(self) -> bool:
        token = self.server.cfg.auth_token.strip()
        if not token:
            return True
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        query_token = query.get("token", [""])[0]
        header_token = self.headers.get("X-Auth-Token", "")
        if query_token == token or header_token == token:
            return True
        self.send_json(HTTPStatus.UNAUTHORIZED, {"error": "invalid or missing auth token"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self.send_html(INDEX_HTML)
            return
        if path == "/api/health":
            self.send_json(HTTPStatus.OK, {"ok": True})
            return
        if not self.is_authorized():
            return

        try:
            if path == "/api/config":
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "device": self.server.cfg.device,
                        "env_file": str(self.server.cfg.env_file),
                        "sender_service": self.server.cfg.sender_service,
                        "sender_state": sender_state(self.server.cfg.sender_service),
                        "token_required": bool(self.server.cfg.auth_token.strip()),
                    },
                )
                return
            if path == "/api/controls":
                controls = get_controls(self.server.cfg.device)
                self.send_json(HTTPStatus.OK, {"controls": controls})
                return
            if path == "/api/latency":
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "sender_state": sender_state(self.server.cfg.sender_service),
                        "metrics": latest_sender_metrics(self.server.cfg.sender_service),
                    },
                )
                return
            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            logging.exception("GET %s failed", path)
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if not self.is_authorized():
            return

        try:
            body = self.read_json()
            if path == "/api/apply":
                values_raw = body.get("values", {})
                if not isinstance(values_raw, dict):
                    raise CameraControlError("values must be an object")
                controls = get_controls(self.server.cfg.device)
                applied, failed = apply_control_values(
                    self.server.cfg.device,
                    controls,
                    values_raw,
                )
                persisted_spec = None
                if bool(body.get("persist")) and applied:
                    persisted_spec = persist_controls(self.server.cfg.env_file, applied)
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "applied": applied,
                        "failed": failed,
                        "persisted_spec": persisted_spec,
                    },
                )
                return

            if path == "/api/preset":
                name = str(body.get("name", "")).strip().lower()
                if name == "manual":
                    values = manual_preset_values()
                elif name == "auto":
                    values = auto_preset_values()
                else:
                    raise CameraControlError("preset must be 'manual' or 'auto'")

                controls = get_controls(self.server.cfg.device)
                applied, failed = apply_control_values(
                    self.server.cfg.device,
                    controls,
                    values,
                )
                persisted_spec = None
                if bool(body.get("persist", True)) and applied:
                    persisted_spec = persist_controls(self.server.cfg.env_file, applied)
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "applied": applied,
                        "failed": failed,
                        "persisted_spec": persisted_spec,
                    },
                )
                return

            if path == "/api/persist-current":
                controls = get_controls(self.server.cfg.device)
                values: dict[str, Any] = {}
                for ctrl in controls:
                    if ctrl.get("read_only"):
                        continue
                    value = ctrl.get("value")
                    if value is None:
                        continue
                    values[str(ctrl["name"])] = value
                spec = persist_controls(self.server.cfg.env_file, values)
                self.send_json(HTTPStatus.OK, {"persisted_spec": spec, "count": len(values)})
                return

            if path == "/api/restart-sender":
                run_command(["systemctl", "restart", self.server.cfg.sender_service])
                self.send_json(
                    HTTPStatus.OK,
                    {"sender_state": sender_state(self.server.cfg.sender_service)},
                )
                return

            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except CameraControlError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logging.exception("POST %s failed", path)
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="USB camera control web UI")
    parser.add_argument(
        "--host",
        default=os.getenv("HMDI_CAMERA_UI_HOST", "0.0.0.0"),
        help="Bind host",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("HMDI_CAMERA_UI_PORT", "8787")),
        help="Bind port",
    )
    parser.add_argument(
        "--device",
        default=os.getenv("HMDI_VIDEO_DEVICE", os.getenv("VIDEO_DEV", "/dev/video0")),
        help="V4L2 video device",
    )
    parser.add_argument(
        "--env-file",
        default=os.getenv("HMDI_ENV_FILE", "/etc/hmdistreamer/hmdistreamer.env"),
        help="Path to hmdistreamer env file",
    )
    parser.add_argument(
        "--sender-service",
        default=os.getenv("HMDI_SENDER_SERVICE", "hmdistreamer-ndi-sender.service"),
        help="Sender service name for restart action",
    )
    parser.add_argument(
        "--auth-token",
        default=os.getenv("HMDI_CAMERA_UI_TOKEN", ""),
        help="Optional API token (clients must send X-Auth-Token)",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("HMDI_CAMERA_UI_LOG_LEVEL", "INFO"),
        help="Log level",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    level = getattr(logging, str(args.log_level).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    if not Path(args.device).exists():
        logging.error("Video device does not exist: %s", args.device)
        return 2

    cfg = UIServerConfig(
        device=args.device,
        env_file=Path(args.env_file),
        sender_service=args.sender_service,
        auth_token=args.auth_token,
    )

    server = CameraControlHTTPServer((args.host, args.port), cfg)
    logging.info(
        "Camera control UI listening on http://%s:%d (device=%s token_required=%s)",
        args.host,
        args.port,
        args.device,
        bool(args.auth_token.strip()),
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Received keyboard interrupt; shutting down")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
