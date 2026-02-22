#!/usr/bin/env python3

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
import os
import shutil
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
SAFE_MODE_NAME_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
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


def env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    return normalized not in {"0", "false", "no", "off"}


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HDMIStreamer Video Controls</title>
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
      <h1>Video Controls</h1>
      <div class="meta" id="meta">Loading...</div>
      <div class="toolbar">
        <button id="refreshBtn">Refresh</button>
        <button id="applyBtn" class="primary">Apply All</button>
        <button id="manualBtn" class="ok">Preset: Manual</button>
        <button id="autoBtn">Preset: Auto</button>
        <button id="persistBtn">Persist Current</button>
        <button id="restartBtn" class="warn">Restart NDI Sender</button>
        <label class="meta"><input id="autoApplyToggle" type="checkbox" checked> auto-apply on change</label>
        <label id="persistToggleLabel" class="meta"><input id="persistToggle" type="checkbox" checked> persist on change/apply/preset</label>
      </div>
      <div class="toolbar">
        <label class="meta">API token (optional):</label>
        <input id="tokenInput" type="text" placeholder="X-Auth-Token" style="max-width:260px">
        <button id="saveTokenBtn">Use Token</button>
      </div>
      <div id="status" class="status"></div>
    </div>

    <div class="card" id="modeCard" style="display:none">
      <div class="meta">Capture mode/profile (may restart sender)</div>
      <div class="toolbar">
        <select id="modeSelect" style="max-width:260px"></select>
        <button id="modeSwitchBtn" class="ok">Switch Mode</button>
      </div>
      <div class="meta" id="modeCurrent">current: -</div>
      <div class="meta mono" id="modeDetails"></div>
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
      persistToggleLabel: document.getElementById("persistToggleLabel"),
      persistToggle: document.getElementById("persistToggle"),
      tokenInput: document.getElementById("tokenInput"),
      modeCard: document.getElementById("modeCard"),
      modeSelect: document.getElementById("modeSelect"),
      modeSwitchBtn: document.getElementById("modeSwitchBtn"),
      modeCurrent: document.getElementById("modeCurrent"),
      modeDetails: document.getElementById("modeDetails"),
      refreshBtn: document.getElementById("refreshBtn"),
      applyBtn: document.getElementById("applyBtn"),
      manualBtn: document.getElementById("manualBtn"),
      autoBtn: document.getElementById("autoBtn"),
      persistBtn: document.getElementById("persistBtn"),
      restartBtn: document.getElementById("restartBtn"),
      saveTokenBtn: document.getElementById("saveTokenBtn"),
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

    function supportsPersist() {
      return Boolean(state.config && state.config.features && state.config.features.persist);
    }

    function supportsPresets() {
      return Boolean(state.config && state.config.features && state.config.features.presets);
    }

    function supportsModeSwitch() {
      return Boolean(state.config && state.config.features && state.config.features.mode_switch);
    }

    function persistRequested() {
      return supportsPersist() && el.persistToggle.checked;
    }

    function updateFeatureVisibility() {
      const persistVisible = supportsPersist();
      const presetsVisible = supportsPresets();
      const modeVisible = supportsModeSwitch();
      el.manualBtn.style.display = presetsVisible ? "" : "none";
      el.autoBtn.style.display = presetsVisible ? "" : "none";
      el.persistBtn.style.display = persistVisible ? "" : "none";
      el.persistToggleLabel.style.display = persistVisible ? "" : "none";
      el.modeCard.style.display = modeVisible ? "" : "none";
      if (!persistVisible) {
        el.persistToggle.checked = false;
      }
    }

    async function refreshModes() {
      const data = await api("/api/modes");
      const enabled = Boolean(data.enabled && data.command_exists && (data.profiles || []).length);
      if (!enabled) {
        el.modeCard.style.display = "none";
        return;
      }

      const profiles = data.profiles || [];
      const selected = data.current_profile || profiles[0] || "";
      const options = profiles.map(name => {
        const sel = name === selected ? "selected" : "";
        return `<option value="${name}" ${sel}>${name}</option>`;
      }).join("");
      el.modeSelect.innerHTML = options;
      el.modeCurrent.textContent = `current: ${data.current_profile || "unknown"} | mode=${data.mode_text || "unknown"}`;
      el.modeDetails.textContent = data.pipeline_excerpt ? `pipeline: ${data.pipeline_excerpt}` : "";
      el.modeCard.style.display = "";
    }

    async function applyMode() {
      if (!supportsModeSwitch()) {
        setStatus("Mode switching is disabled", false);
        return;
      }
      const profile = (el.modeSelect.value || "").trim();
      if (!profile) {
        setStatus("Select a mode profile first", false);
        return;
      }
      el.modeSwitchBtn.disabled = true;
      try {
        const data = await api("/api/modes/apply", {
          method: "POST",
          body: JSON.stringify({ profile }),
        });
        setStatus(`Switched mode to ${data.profile}; sender=${data.sender_state}`, true);
      } finally {
        el.modeSwitchBtn.disabled = false;
      }
      await refreshAll();
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
      const persist = persistRequested();
      const payload = { values: { [control.name]: value }, persist };
      const data = await api("/api/apply", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      if (data.persist_error) {
        setStatus(`Applied ${control.name}=${value}; persist failed`, false);
        await refreshAll();
        return;
      }
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
      updateFeatureVisibility();
      const presetsSummary = (config.preset_names || []).join(",") || "off";
      const persistSummary = config.features && config.features.persist ? "on" : "off";
      const modeSummary = config.features && config.features.mode_switch ? "on" : "off";
      el.meta.textContent = `device=${config.device} | sender=${config.sender_service} (${config.sender_state}) | presets=${presetsSummary} | persist=${persistSummary} | mode_switch=${modeSummary} | token_required=${config.token_required}`;
      await refreshModes();
      const data = await api("/api/controls");
      state.controls = data.controls || [];
      renderControls();
      await refreshLatency();
      setStatus("Loaded controls", true);
    }

    async function applyAll() {
      const values = collectValues();
      const persist = persistRequested();
      const data = await api("/api/apply", {
        method: "POST",
        body: JSON.stringify({ values, persist }),
      });
      if (data.persist_error) {
        setStatus("Applied controls but persist failed", false);
        await refreshAll();
        return;
      }
      const failed = Object.keys(data.failed || {}).length;
      if (failed) {
        setStatus(`Applied with ${failed} failures`, false);
      } else {
        setStatus(`Applied ${Object.keys(data.applied || {}).length} controls`, true);
      }
      await refreshAll();
    }

    async function applyPreset(name) {
      if (!supportsPresets()) {
        setStatus("Presets are disabled", false);
        return;
      }
      const persist = persistRequested();
      const data = await api("/api/preset", {
        method: "POST",
        body: JSON.stringify({ name, persist }),
      });
      if (data.persist_error) {
        setStatus(`Preset ${name} applied but persist failed`, false);
        await refreshAll();
        return;
      }
      const failed = Object.keys(data.failed || {}).length;
      if (failed) {
        setStatus(`Preset ${name}: ${failed} failures`, false);
      } else {
        setStatus(`Preset ${name} applied`, true);
      }
      await refreshAll();
    }

    async function persistCurrent() {
      if (!supportsPersist()) {
        setStatus("Persistence is disabled", false);
        return;
      }
      await api("/api/persist-current", { method: "POST", body: "{}" });
      setStatus("Persisted current controls to startup defaults", true);
    }

    async function restartSender() {
      const data = await api("/api/restart-sender", { method: "POST", body: "{}" });
      setStatus(`Sender restart requested; state=${data.sender_state}`, true);
      await refreshAll();
    }

    el.refreshBtn.onclick = () => refreshAll().catch(err => setStatus(err.message, false));
    el.applyBtn.onclick = () => applyAll().catch(err => setStatus(err.message, false));
    el.manualBtn.onclick = () => applyPreset("manual").catch(err => setStatus(err.message, false));
    el.autoBtn.onclick = () => applyPreset("auto").catch(err => setStatus(err.message, false));
    el.persistBtn.onclick = () => persistCurrent().catch(err => setStatus(err.message, false));
    el.restartBtn.onclick = () => restartSender().catch(err => setStatus(err.message, false));
    el.modeSwitchBtn.onclick = () => applyMode().catch(err => setStatus(err.message, false));
    el.saveTokenBtn.onclick = () => {
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
    command_timeout_sec: float
    max_request_bytes: int
    persist_enabled: bool
    persist_enable_key: str
    persist_preset_key: str
    persist_setctrls_key: str
    persist_preset_value: str
    presets: dict[str, dict[str, Any]]
    mode_switch_enabled: bool
    mode_switch_command: str
    mode_switch_args: list[str]
    mode_switch_profiles: list[str]
    mode_switch_timeout_sec: float


class CameraControlHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], cfg: UIServerConfig) -> None:
        super().__init__(server_address, CameraControlHandler)
        self.cfg = cfg


def run_command(command: list[str], timeout_sec: float = 5.0) -> str:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except FileNotFoundError as exc:
        raise CameraControlError(f"Command not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CameraControlError(
            f"Command timed out after {timeout_sec:.1f}s: {' '.join(command)}"
        ) from exc

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


def get_controls(device: str, timeout_sec: float) -> list[dict[str, Any]]:
    text = run_command(["v4l2-ctl", "-d", device, "--list-ctrls-menus"], timeout_sec)
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
    timeout_sec: float,
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
            run_command(
                ["v4l2-ctl", "-d", device, "--set-ctrl", f"{name}={value}"],
                timeout_sec,
            )
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


def persist_controls(cfg: UIServerConfig, values: dict[str, Any]) -> str:
    if not cfg.persist_enabled:
        raise CameraControlError("persistence is disabled")
    if not cfg.persist_setctrls_key.strip():
        raise CameraControlError("persistence key for controls is not configured")

    spec = ",".join(f"{name}={values[name]}" for name in sorted(values))
    if cfg.persist_enable_key.strip():
        upsert_env_key(cfg.env_file, cfg.persist_enable_key, "1")
    if cfg.persist_preset_key.strip() and cfg.persist_preset_value.strip():
        upsert_env_key(cfg.env_file, cfg.persist_preset_key, cfg.persist_preset_value)
    upsert_env_key(cfg.env_file, cfg.persist_setctrls_key, spec)
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


def parse_preset_json(raw: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CameraControlError(f"{label} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise CameraControlError(f"{label} must be a JSON object")

    out: dict[str, Any] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not SAFE_CONTROL_RE.match(key):
            raise CameraControlError(f"{label} contains invalid control name: {key!r}")
        out[key] = value
    return out


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    out: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if not value:
            out[key] = ""
            continue
        try:
            parts = shlex.split(value, posix=True)
        except ValueError:
            out[key] = value
            continue
        out[key] = parts[0] if len(parts) == 1 else value
    return out


def _command_exists(command: str) -> bool:
    if not command.strip():
        return False
    if "/" in command:
        return os.path.isfile(command) and os.access(command, os.X_OK)
    return shutil.which(command) is not None


def parse_mode_profiles(raw: str) -> list[str]:
    out: list[str] = []
    for part in raw.split(","):
        profile = part.strip()
        if not profile:
            continue
        if not SAFE_MODE_NAME_RE.match(profile):
            raise CameraControlError(f"invalid mode profile name: {profile!r}")
        out.append(profile)
    return out


def infer_active_profile(cfg: UIServerConfig, env_map: dict[str, str]) -> str | None:
    profile = env_map.get("HMDI_USB_PROFILE", "").strip().lower()
    if profile and profile in cfg.mode_switch_profiles:
        return profile

    width = env_map.get("HMDI_WIDTH", "").strip()
    height = env_map.get("HMDI_HEIGHT", "").strip()
    fps_num = env_map.get("HMDI_FPS_NUM", "").strip()
    fps_den = env_map.get("HMDI_FPS_DEN", "").strip()
    pipeline = env_map.get("HMDI_GST_SOURCE_PIPELINE", "")
    lower_pipeline = pipeline.lower()

    if (
        width == "1280"
        and height == "720"
        and fps_num == "30"
        and fps_den == "1"
        and "width=1280" in lower_pipeline
        and "height=720" in lower_pipeline
        and "image/jpeg" in lower_pipeline
        and "microscope-latency" in cfg.mode_switch_profiles
    ):
        return "microscope-latency"

    if (
        width == "1600"
        and height == "1200"
        and fps_num == "30"
        and fps_den == "1"
        and "width=1600" in lower_pipeline
        and "height=1200" in lower_pipeline
        and "image/jpeg" in lower_pipeline
        and "microscope-detail" in cfg.mode_switch_profiles
    ):
        return "microscope-detail"

    return None


def mode_status(cfg: UIServerConfig) -> dict[str, Any]:
    env_map = parse_env_file(cfg.env_file)
    width = env_map.get("HMDI_WIDTH", "").strip()
    height = env_map.get("HMDI_HEIGHT", "").strip()
    fps_num = env_map.get("HMDI_FPS_NUM", "").strip()
    fps_den = env_map.get("HMDI_FPS_DEN", "").strip()

    mode_parts: list[str] = []
    if width and height:
        mode_parts.append(f"{width}x{height}")
    if fps_num and fps_den:
        mode_parts.append(f"@{fps_num}/{fps_den}fps")
    mode_text = " ".join(mode_parts) if mode_parts else "unknown"

    pipeline = env_map.get("HMDI_GST_SOURCE_PIPELINE", "")
    pipeline_excerpt = pipeline if len(pipeline) <= 180 else pipeline[:177] + "..."

    return {
        "enabled": cfg.mode_switch_enabled,
        "command": cfg.mode_switch_command,
        "command_exists": _command_exists(cfg.mode_switch_command),
        "profiles": list(cfg.mode_switch_profiles),
        "current_profile": infer_active_profile(cfg, env_map),
        "mode_text": mode_text,
        "pipeline_excerpt": pipeline_excerpt,
    }


def sender_state(service_name: str, timeout_sec: float) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unknown"

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


def latest_sender_metrics(service_name: str, timeout_sec: float) -> dict[str, Any] | None:
    try:
        text = run_command(
            ["journalctl", "-u", service_name, "-n", "120", "--no-pager"],
            timeout_sec,
        )
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
        content_length = self.headers.get("Content-Length", "0")
        try:
            length = int(content_length)
        except ValueError as exc:
            raise CameraControlError("invalid Content-Length") from exc
        if length <= 0:
            return {}
        if length > self.server.cfg.max_request_bytes:
            raise CameraControlError(
                f"request body too large ({length} > {self.server.cfg.max_request_bytes})"
            )
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
                preset_names = sorted(self.server.cfg.presets.keys())
                persist_enabled = (
                    self.server.cfg.persist_enabled
                    and bool(self.server.cfg.persist_setctrls_key.strip())
                )
                mode_info = mode_status(self.server.cfg)
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "device": self.server.cfg.device,
                        "env_file": str(self.server.cfg.env_file),
                        "sender_service": self.server.cfg.sender_service,
                        "sender_state": sender_state(
                            self.server.cfg.sender_service,
                            self.server.cfg.command_timeout_sec,
                        ),
                        "token_required": bool(self.server.cfg.auth_token.strip()),
                        "features": {
                            "presets": bool(preset_names),
                            "persist": persist_enabled,
                            "mode_switch": bool(
                                mode_info["enabled"] and mode_info["command_exists"]
                            ),
                        },
                        "preset_names": preset_names,
                    },
                )
                return
            if path == "/api/modes":
                self.send_json(HTTPStatus.OK, mode_status(self.server.cfg))
                return
            if path == "/api/controls":
                controls = get_controls(
                    self.server.cfg.device,
                    self.server.cfg.command_timeout_sec,
                )
                self.send_json(HTTPStatus.OK, {"controls": controls})
                return
            if path == "/api/latency":
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "sender_state": sender_state(
                            self.server.cfg.sender_service,
                            self.server.cfg.command_timeout_sec,
                        ),
                        "metrics": latest_sender_metrics(
                            self.server.cfg.sender_service,
                            self.server.cfg.command_timeout_sec,
                        ),
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
                controls = get_controls(
                    self.server.cfg.device,
                    self.server.cfg.command_timeout_sec,
                )
                applied, failed = apply_control_values(
                    self.server.cfg.device,
                    controls,
                    values_raw,
                    self.server.cfg.command_timeout_sec,
                )
                persisted_spec = None
                persist_error = None
                if bool(body.get("persist")) and applied:
                    try:
                        persisted_spec = persist_controls(self.server.cfg, applied)
                    except CameraControlError as exc:
                        persist_error = str(exc)
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "applied": applied,
                        "failed": failed,
                        "persisted_spec": persisted_spec,
                        "persist_error": persist_error,
                    },
                )
                return

            if path == "/api/preset":
                name = str(body.get("name", "")).strip().lower()
                if not self.server.cfg.presets:
                    raise CameraControlError("presets are disabled")
                if name not in self.server.cfg.presets:
                    supported = ", ".join(sorted(self.server.cfg.presets.keys()))
                    raise CameraControlError(f"unknown preset '{name}' (supported: {supported})")
                values = dict(self.server.cfg.presets[name])

                controls = get_controls(
                    self.server.cfg.device,
                    self.server.cfg.command_timeout_sec,
                )
                applied, failed = apply_control_values(
                    self.server.cfg.device,
                    controls,
                    values,
                    self.server.cfg.command_timeout_sec,
                )
                persisted_spec = None
                persist_error = None
                if bool(body.get("persist", True)) and applied:
                    try:
                        persisted_spec = persist_controls(self.server.cfg, applied)
                    except CameraControlError as exc:
                        persist_error = str(exc)
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "applied": applied,
                        "failed": failed,
                        "persisted_spec": persisted_spec,
                        "persist_error": persist_error,
                    },
                )
                return

            if path == "/api/persist-current":
                controls = get_controls(
                    self.server.cfg.device,
                    self.server.cfg.command_timeout_sec,
                )
                values: dict[str, Any] = {}
                for ctrl in controls:
                    if ctrl.get("read_only"):
                        continue
                    value = ctrl.get("value")
                    if value is None:
                        continue
                    values[str(ctrl["name"])] = value
                spec = persist_controls(self.server.cfg, values)
                self.send_json(HTTPStatus.OK, {"persisted_spec": spec, "count": len(values)})
                return

            if path == "/api/restart-sender":
                run_command(
                    ["systemctl", "restart", self.server.cfg.sender_service],
                    self.server.cfg.command_timeout_sec,
                )
                self.send_json(
                    HTTPStatus.OK,
                    {
                        "sender_state": sender_state(
                            self.server.cfg.sender_service,
                            self.server.cfg.command_timeout_sec,
                        )
                    },
                )
                return

            if path == "/api/modes/apply":
                profile = str(body.get("profile", "")).strip().lower()
                if not self.server.cfg.mode_switch_enabled:
                    raise CameraControlError("mode switching is disabled")
                if not profile:
                    raise CameraControlError("profile is required")
                if profile not in self.server.cfg.mode_switch_profiles:
                    supported = ", ".join(self.server.cfg.mode_switch_profiles)
                    raise CameraControlError(
                        f"unknown profile '{profile}' (supported: {supported})"
                    )
                if not _command_exists(self.server.cfg.mode_switch_command):
                    raise CameraControlError(
                        f"mode switch command not found: {self.server.cfg.mode_switch_command}"
                    )

                command = [self.server.cfg.mode_switch_command, profile]
                command.extend(self.server.cfg.mode_switch_args)
                output = run_command(command, self.server.cfg.mode_switch_timeout_sec)

                self.send_json(
                    HTTPStatus.OK,
                    {
                        "profile": profile,
                        "sender_state": sender_state(
                            self.server.cfg.sender_service,
                            self.server.cfg.command_timeout_sec,
                        ),
                        "mode": mode_status(self.server.cfg),
                        "output_excerpt": output[-2000:] if len(output) > 2000 else output,
                    },
                )
                return

            self.send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        except CameraControlError as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logging.exception("POST %s failed", path)
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HDMIStreamer video control web UI")
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
        "--command-timeout-sec",
        type=float,
        default=float(os.getenv("HMDI_CAMERA_UI_CMD_TIMEOUT_SEC", "5.0")),
        help="Timeout for external commands",
    )
    parser.add_argument(
        "--max-request-bytes",
        type=int,
        default=int(os.getenv("HMDI_CAMERA_UI_MAX_REQUEST_BYTES", "65536")),
        help="Maximum accepted JSON request body size",
    )
    parser.add_argument(
        "--disable-presets",
        action="store_true",
        default=not env_flag("HMDI_CAMERA_UI_ENABLE_PRESETS", True),
        help="Disable preset endpoints and buttons",
    )
    parser.add_argument(
        "--disable-persist",
        action="store_true",
        default=not env_flag("HMDI_CAMERA_UI_ENABLE_PERSIST", True),
        help="Disable startup-persistence actions",
    )
    parser.add_argument(
        "--disable-mode-switch",
        action="store_true",
        default=not env_flag("HMDI_CAMERA_UI_ENABLE_MODE_SWITCH", True),
        help="Disable capture mode switch actions",
    )
    parser.add_argument(
        "--mode-switch-command",
        default=os.getenv(
            "HMDI_CAMERA_UI_MODE_COMMAND",
            "/usr/local/bin/hmdistreamer-set-usb-profile",
        ),
        help="Command used to switch capture profiles",
    )
    parser.add_argument(
        "--mode-switch-profiles",
        default=os.getenv(
            "HMDI_CAMERA_UI_MODE_PROFILES",
            "microscope-latency,microscope-detail",
        ),
        help="Comma-separated allowed profile names",
    )
    parser.add_argument(
        "--mode-switch-args",
        default=os.getenv("HMDI_CAMERA_UI_MODE_ARGS", ""),
        help="Extra args appended after selected profile",
    )
    parser.add_argument(
        "--mode-switch-timeout-sec",
        type=float,
        default=float(os.getenv("HMDI_CAMERA_UI_MODE_TIMEOUT_SEC", "20.0")),
        help="Timeout for mode switch command",
    )
    parser.add_argument(
        "--persist-enable-key",
        default=os.getenv("HMDI_CAMERA_UI_PERSIST_ENABLE_KEY", "HMDI_USB_APPLY_CONTROLS"),
        help="Env key to mark startup control application enabled",
    )
    parser.add_argument(
        "--persist-preset-key",
        default=os.getenv("HMDI_CAMERA_UI_PERSIST_PRESET_KEY", "HMDI_USB_CONTROL_PRESET"),
        help="Env key used to record selected preset name",
    )
    parser.add_argument(
        "--persist-setctrls-key",
        default=os.getenv("HMDI_CAMERA_UI_PERSIST_SETCTRLS_KEY", "HMDI_USB_SET_CTRLS"),
        help="Env key used to persist control=value pairs",
    )
    parser.add_argument(
        "--persist-preset-value",
        default=os.getenv("HMDI_CAMERA_UI_PERSIST_PRESET_VALUE", "manual"),
        help="Preset value persisted alongside control map",
    )
    parser.add_argument(
        "--preset-manual-json",
        default=os.getenv("HMDI_CAMERA_UI_PRESET_MANUAL_JSON", ""),
        help="Override manual preset with JSON object",
    )
    parser.add_argument(
        "--preset-auto-json",
        default=os.getenv("HMDI_CAMERA_UI_PRESET_AUTO_JSON", ""),
        help="Override auto preset with JSON object",
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
    if shutil.which("v4l2-ctl") is None:
        logging.error("Missing required command: v4l2-ctl")
        return 2
    if args.command_timeout_sec <= 0:
        logging.error("Invalid --command-timeout-sec: %s", args.command_timeout_sec)
        return 2
    if args.mode_switch_timeout_sec <= 0:
        logging.error(
            "Invalid --mode-switch-timeout-sec: %s", args.mode_switch_timeout_sec
        )
        return 2
    if args.max_request_bytes <= 0:
        logging.error("Invalid --max-request-bytes: %s", args.max_request_bytes)
        return 2

    manual_values = manual_preset_values()
    auto_values = auto_preset_values()
    if str(args.preset_manual_json).strip():
        manual_values = parse_preset_json(args.preset_manual_json, "--preset-manual-json")
    if str(args.preset_auto_json).strip():
        auto_values = parse_preset_json(args.preset_auto_json, "--preset-auto-json")

    presets: dict[str, dict[str, Any]] = {}
    if not args.disable_presets:
        presets["manual"] = manual_values
        presets["auto"] = auto_values

    try:
        mode_profiles = parse_mode_profiles(str(args.mode_switch_profiles))
    except CameraControlError as exc:
        logging.error("%s", exc)
        return 2
    if not args.disable_mode_switch and not mode_profiles:
        logging.error(
            "Mode switching is enabled but no profiles were configured. "
            "Set HMDI_CAMERA_UI_MODE_PROFILES or use --disable-mode-switch."
        )
        return 2
    try:
        mode_args = shlex.split(str(args.mode_switch_args), posix=True)
    except ValueError as exc:
        logging.error("Invalid --mode-switch-args value: %s", exc)
        return 2

    cfg = UIServerConfig(
        device=args.device,
        env_file=Path(args.env_file),
        sender_service=args.sender_service,
        auth_token=args.auth_token,
        command_timeout_sec=float(args.command_timeout_sec),
        max_request_bytes=int(args.max_request_bytes),
        persist_enabled=not bool(args.disable_persist),
        persist_enable_key=str(args.persist_enable_key).strip(),
        persist_preset_key=str(args.persist_preset_key).strip(),
        persist_setctrls_key=str(args.persist_setctrls_key).strip(),
        persist_preset_value=str(args.persist_preset_value).strip(),
        presets=presets,
        mode_switch_enabled=not bool(args.disable_mode_switch),
        mode_switch_command=str(args.mode_switch_command).strip(),
        mode_switch_args=mode_args,
        mode_switch_profiles=mode_profiles,
        mode_switch_timeout_sec=float(args.mode_switch_timeout_sec),
    )
    if cfg.mode_switch_enabled and not _command_exists(cfg.mode_switch_command):
        logging.warning(
            "Mode switch command is not available: %s", cfg.mode_switch_command
        )

    server = CameraControlHTTPServer((args.host, args.port), cfg)
    logging.info(
        "Camera control UI listening on http://%s:%d "
        "(device=%s token_required=%s presets=%s persist=%s mode_switch=%s)",
        args.host,
        args.port,
        args.device,
        bool(args.auth_token.strip()),
        ",".join(sorted(cfg.presets.keys())) if cfg.presets else "off",
        cfg.persist_enabled and bool(cfg.persist_setctrls_key),
        cfg.mode_switch_enabled and bool(cfg.mode_switch_profiles),
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
