"""Web dashboard for controlling and monitoring a Minecraft systemd service.

This app provides:
- Service controls (start/stop/manual backup)
- Live server and Minecraft stats
- Systemd log viewer
- Automatic idle shutdown and session-based backup scheduling
"""

from flask import Flask, render_template_string, redirect, request, jsonify, Response, stream_with_context
import subprocess
from pathlib import Path
from datetime import datetime
import time
import threading
import re
import shutil
from zoneinfo import ZoneInfo

app = Flask(__name__)

# Core service/application settings.
SERVICE = "minecraft"
BACKUP_SCRIPT = "/opt/Minecraft/backup.sh"
BACKUP_DIR = Path("/home/marites/backups")
SESSION_FILE = Path(__file__).resolve().parent / "session.txt"
# "PST" here means Philippines Standard Time (UTC+8), not Pacific Time.
DISPLAY_TZ = ZoneInfo("Asia/Manila")
SUDO_PASSWORD = "SuperCute"
RCON_PASSWORD = "SuperCute"
RCON_HOST = "127.0.0.1"
RCON_PORT = 25575
SERVER_PROPERTIES_CANDIDATES = [
    Path("/opt/Minecraft/server.properties"),
    Path("/opt/Minecraft/server/server.properties"),
    Path(__file__).resolve().parent / "server.properties",
]

# Backup/automation timing controls.
BACKUP_INTERVAL_HOURS = 6
BACKUP_INTERVAL_SECONDS = max(60, int(BACKUP_INTERVAL_HOURS * 3600))
IDLE_ZERO_PLAYERS_SECONDS = 180
IDLE_CHECK_INTERVAL_SECONDS = 15

# Shared watcher state (protected by locks below).
idle_zero_players_since = None
idle_lock = threading.Lock()
backup_periodic_runs = 0
backup_lock = threading.Lock()
backup_active_jobs = 0
backup_last_error = ""
backup_waiting_for_last_change = False
backup_waiting_baseline_snapshot = {}
backup_last_successful_at = None
session_tracking_initialized = False
session_tracking_lock = threading.Lock()
service_status_intent = None
service_status_intent_lock = threading.Lock()

OFF_STATES = {"inactive", "failed"}

# Cache Minecraft runtime probes so rapid UI polling does not spam RCON.
MC_QUERY_INTERVAL_SECONDS = 3
mc_query_lock = threading.Lock()
mc_last_query_at = 0.0
mc_cached_players_online = "unknown"
mc_cached_tick_rate = "unknown"
rcon_config_lock = threading.Lock()
rcon_cached_password = RCON_PASSWORD
rcon_cached_port = RCON_PORT
rcon_last_config_read_at = 0.0

# Single-file HTML template for the dashboard UI.
HTML = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Minecraft Control</title>
<style>
    :root {
        --bg: #f4f6f8;
        --surface: #ffffff;
        --border: #d8dee6;
        --text: #1f2a37;
        --muted: #5a6878;
        --accent: #1e40af;
        --accent-hover: #1b3a9a;
        --ok: #166534;
        --warn: #92400e;
        --console-bg: #0b1220;
        --console-border: #1f2d45;
        --console-text: #d2e4ff;
    }

    * { box-sizing: border-box; }

    html, body {
        height: 100%;
        overflow: hidden;
    }

    body {
        margin: 0;
        font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
        background: linear-gradient(180deg, #f7f9fb 0%, #edf2f7 100%);
        color: var(--text);
    }

    .container {
        max-width: 1200px;
        margin: 0 auto;
        height: 100dvh;
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 12px;
        overflow: hidden;
    }

    .header {
        display: flex;
        flex-wrap: wrap;
        gap: 16px;
        justify-content: space-between;
        align-items: center;
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: 14px;
        padding: 18px 20px;
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.06);
    }

    .title h1 {
        margin: 0;
        font-size: 1.4rem;
        letter-spacing: 0.2px;
    }

    .title {
        width: 100%;
        min-width: 0;
    }

    .title-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        margin-bottom: 8px;
    }

    .stats-groups {
        display: grid;
        grid-template-columns: minmax(420px, 1.35fr) minmax(260px, 1fr) minmax(260px, 1fr);
        gap: 10px;
        width: 100%;
    }

    .stats-group {
        border: 1px solid var(--border);
        border-radius: 10px;
        background: #f8fafc;
        padding: 8px 10px;
        min-width: 0;
    }

    .server-stats {
        min-width: 420px;
    }

    .group-title {
        margin: 0 0 6px 0;
        font-size: 0.78rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        color: #64748b;
        font-weight: 700;
    }

    .status-row {
        display: flex;
        flex-direction: column;
        align-items: flex-start;
        gap: 4px;
        color: var(--muted);
        font-size: 0.88rem;
    }

    .status-row b {
        color: var(--text);
        font-variant-numeric: tabular-nums;
    }

    .stat-green { color: #166534 !important; }
    .stat-yellow { color: #a16207 !important; }
    .stat-orange { color: #c2410c !important; }
    .stat-red { color: #b91c1c !important; }

    .status-row span {
        white-space: nowrap;
    }

    .actions {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        align-items: center;
    }

    .server-time {
        color: var(--muted);
        font-size: 0.9rem;
        font-variant-numeric: tabular-nums;
        white-space: nowrap;
    }

    .actions form {
        margin: 0;
    }

    button {
        border: 0;
        border-radius: 10px;
        padding: 10px 16px;
        font-weight: 600;
        cursor: pointer;
        transition: transform 0.06s ease, opacity 0.16s ease, background 0.16s ease;
        color: #fff;
        background: var(--accent);
    }

    button:hover { background: var(--accent-hover); }
    button:active { transform: translateY(1px); }
    button:disabled {
        background: #94a3b8;
        cursor: not-allowed;
        opacity: 0.65;
        transform: none;
    }

    .btn-stop { background: #b91c1c; }
    .btn-stop:hover { background: #991b1b; }

    .btn-backup { background: #0f766e; }
    .btn-backup:hover { background: #0d5f59; }

    .logs {
        display: block;
        flex: 1;
        min-height: 0;
        overflow: hidden;
    }

    .panel {
        border: 1px solid var(--border);
        border-radius: 14px;
        background: var(--surface);
        overflow: hidden;
        box-shadow: 0 6px 16px rgba(15, 23, 42, 0.05);
        display: flex;
        flex-direction: column;
        min-height: 0;
        height: 100%;
    }

    .panel-header {
        display: flex;
        justify-content: space-between;
        gap: 8px;
        align-items: center;
        padding: 10px 12px;
        border-bottom: 1px solid var(--border);
        background: #f8fafc;
    }

    .panel-header h3 {
        margin: 0;
        font-size: 0.98rem;
        color: #334155;
        white-space: nowrap;
    }

    .panel-controls {
        display: flex;
        gap: 8px;
        align-items: center;
        flex: 1;
        justify-content: flex-end;
    }

    .panel-filter {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 0.84rem;
        color: #475569;
        white-space: nowrap;
    }

    .panel-controls input[type="text"],
    .panel-controls select {
        flex: 1;
        min-width: 0;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        padding: 8px 10px;
        font-size: 0.9rem;
        color: #0f172a;
        background: #ffffff;
    }

    .panel-controls input[type="text"]:disabled {
        background: #e2e8f0;
        color: #64748b;
        cursor: not-allowed;
    }

    .console-box {
        margin: 0;
        min-height: 0;
        max-height: none;
        overflow: auto;
        white-space: pre-wrap;
        word-break: break-word;
        padding: 14px;
        font-size: 0.86rem;
        line-height: 1.45;
        border-top: 1px solid var(--console-border);
        background: linear-gradient(180deg, #0b1220 0%, #0e1627 100%);
        color: var(--console-text);
        flex: 1;
    }

    .modal-overlay {
        position: fixed;
        inset: 0;
        background: rgba(15, 23, 42, 0.55);
        display: none;
        align-items: center;
        justify-content: center;
        z-index: 9999;
        padding: 16px;
    }

    .modal-overlay.open {
        display: flex;
    }

    .modal-card {
        width: min(420px, 100%);
        background: #ffffff;
        border: 1px solid var(--border);
        border-radius: 12px;
        box-shadow: 0 18px 40px rgba(2, 6, 23, 0.28);
        padding: 14px;
    }

    .modal-title {
        margin: 0 0 8px 0;
        font-size: 1rem;
        color: #0f172a;
    }

    .modal-text {
        margin: 0 0 12px 0;
        font-size: 0.9rem;
        color: #334155;
    }

    .modal-image {
        width: 100%;
        max-height: 220px;
        object-fit: cover;
        border-radius: 8px;
        border: 1px solid #cbd5e1;
        margin: 0 0 12px 0;
    }

    .modal-input {
        width: 100%;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
        padding: 8px 10px;
        font-size: 0.92rem;
        margin-bottom: 12px;
    }

    .modal-actions {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
    }

    .btn-secondary {
        background: #475569;
    }

    .btn-secondary:hover {
        background: #334155;
    }

    @media (max-width: 900px) {
        .title-row {
            flex-direction: column;
            align-items: flex-start;
        }

        .actions {
            justify-content: flex-start;
        }

        .stats-groups {
            grid-template-columns: 1fr;
        }

        .server-stats {
            min-width: 0;
        }

        .logs {
            display: block;
        }

        .panel-header {
            flex-direction: column;
            align-items: stretch;
        }

        .panel-header h3 {
            white-space: normal;
        }
    }
</style>
</head>
<body>
<div class="container">
    <!-- Header area: title, action buttons, and all stat cards. -->
    <section class="header">
        <div class="title">
            <div class="title-row">
                <h1>Marites Server Control</h1>
                <div class="actions">
                    <span class="server-time">Server time: <b id="server-time">{{ server_time }}</b></span>
                    <form class="ajax-form" method="post" action="/start">
                        <button id="start-btn" type="submit" {% if service_running_status == "active" %}disabled{% endif %}>Start</button>
                    </form>
                    <form class="ajax-form sudo-form" method="post" action="/stop">
                        <input type="hidden" name="sudo_password">
                        <button id="stop-btn" class="btn-stop" type="submit" {% if service_running_status != "active" %}disabled{% endif %}>Stop</button>
                    </form>
                    <form class="ajax-form" method="post" action="/backup">
                        <button class="btn-backup" type="submit">Backup</button>
                    </form>
                </div>
            </div>
            <div class="stats-groups">
                <!-- Host machine utilization metrics. -->
                <div class="stats-group server-stats">
                    <p class="group-title">Server Stats</p>
                    <div class="status-row">
                        <span>RAM: <b id="ram-usage" class="{{ ram_usage_class }}">{{ ram_usage }}</b></span>
                        <span>CPU: <b id="cpu-per-core">{% for core in cpu_per_core_items %}<span class="{{ core.class }}">CPU{{ core.index }} {{ core.value }}%</span>{% if not loop.last %} | {% endif %}{% endfor %}</b></span>
                        <span>CPU freq: <b id="cpu-frequency" class="{{ cpu_frequency_class }}">{{ cpu_frequency }}</b></span>
                        <span>Storage: <b id="storage-usage" class="{{ storage_usage_class }}">{{ storage_usage }}</b></span>
                    </div>
                </div>
                <!-- Minecraft runtime/health metrics. -->
                <div class="stats-group">
                    <p class="group-title">Minecraft Stats</p>
                    <div class="status-row">
                        <span>Server Status: <b id="service-status" class="{{ service_status_class }}">{{ service_status }}</b><span id="service-status-duration-prefix">{% if service_status == "Running" and session_duration != "--" %} for {% endif %}</span><b id="session-duration" {% if service_status != "Running" or session_duration == "--" %}style="display:none;"{% endif %}>{{ session_duration }}</b></span>
                        <span>Players online: <b id="players-online">{{ players_online }}</b></span>
                        <span>Tick time: <b id="tick-rate">{{ tick_rate }}</b></span>
                        <span>Auto-stop in: <b id="idle-countdown">{{ idle_countdown }}</b></span>
                    </div>
                </div>
                <!-- Backup scheduler/activity metrics. -->
                <div class="stats-group">
                    <p class="group-title">Backup Stats</p>
                    <div class="status-row">
                        <span>Backup status: <b id="backup-status" class="{{ backup_status_class }}">{{ backup_status }}</b></span>
                        <span>Last backup: <b id="last-backup-time">{{ last_backup_time }}</b></span>
                        <span>Next backup: <b id="next-backup-time">{{ next_backup_time }}</b></span>
                        <span>Backups folder: <b id="backups-status">{{ backups_status }}</b></span>
                    </div>
                </div>
            </div>
        </div>

    </section>

    <!-- Main content: filtered Minecraft service logs. -->
    <section class="logs">
        <article class="panel">
            <div class="panel-header">
                <h3>Minecraft Log</h3>
                <form class="panel-controls ajax-form sudo-form" method="post" action="/rcon">
                    <label class="panel-filter">
                        <input id="hide-rcon-noise" type="checkbox" checked>
                        Hide RCON noise
                    </label>
                    <input type="hidden" name="sudo_password">
                    <input id="rcon-command" type="text" name="rcon_command" placeholder="Enter Minecraft server command" {% if service_running_status != "active" %}disabled{% endif %} required>
                    <button id="rcon-submit" type="submit" {% if service_running_status != "active" %}disabled{% endif %}>Submit</button>
                </form>
            </div>
            <pre id="minecraft-log" class="console-box">{{ minecraft_logs_raw }}</pre>
        </article>
    </section>
</div>
<!-- Password gate modal for privileged operations (stop + RCON submit). -->
<div id="sudo-modal" class="modal-overlay" aria-hidden="true">
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="sudo-modal-title">
        <h3 id="sudo-modal-title" class="modal-title">Password Required</h3>
        <p class="modal-text">Enter sudo password to continue.</p>
        <input id="sudo-modal-input" class="modal-input" type="text" placeholder="Sudo password">
        <div class="modal-actions">
            <button id="sudo-modal-cancel" class="btn-secondary" type="button">Cancel</button>
            <button id="sudo-modal-submit" type="button">Continue</button>
        </div>
    </div>
</div>
<!-- Generic message modal (validation errors, action rejection, etc.). -->
<div id="message-modal" class="modal-overlay" aria-hidden="true">
    <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="message-modal-title">
        <h3 id="message-modal-title" class="modal-title">Action Rejected</h3>
        <img class="modal-image" src="https://i.imgflip.com/6k8gqw.jpg" alt="Incorrect password image">
        <p id="message-modal-text" class="modal-text"></p>
        <div class="modal-actions">
            <button id="message-modal-ok" type="button">OK</button>
        </div>
    </div>
</div>
<script>
    // `alert_message` is set server-side when an action fails validation.
    const alertMessage = {{ alert_message | tojson }};

    // UI state used for dynamic controls/modals.
    let idleCountdownSeconds = null;
    let pendingSudoForm = null;
    let rawMinecraftLogLines = [];
    let logEventSource = null;

    // Refresh cadence configuration (milliseconds).
    // Active mode = full dashboard updates.
    const ACTIVE_METRICS_INTERVAL_MS = 1000;
    const ACTIVE_COUNTDOWN_INTERVAL_MS = 5000;
    // Off mode = only server stats update.
    const OFF_SERVER_STATS_INTERVAL_MS = 15000;

    // Timer handles retained so intervals can be stopped/restarted cleanly.
    let metricsTimer = null;
    let countdownTimer = null;
    // Current scheduler mode: "active" or "off".
    let refreshMode = null;

    function scrollLogToBottom() {
        const target = document.getElementById("minecraft-log");
        if (!target) return;
        target.scrollTop = target.scrollHeight;
    }

    function getRawMinecraftLogText() {
        return rawMinecraftLogLines.join("\\n");
    }

    function setRawMinecraftLogText(rawText) {
        rawMinecraftLogLines = (rawText || "").split("\\n");
        if (rawMinecraftLogLines.length > 500) {
            rawMinecraftLogLines = rawMinecraftLogLines.slice(-500);
        }
    }

    function appendRawMinecraftLogLine(line) {
        rawMinecraftLogLines.push(line || "");
        if (rawMinecraftLogLines.length > 500) {
            rawMinecraftLogLines.shift();
        }
    }

    function filterMinecraftLog(rawText) {
        const hideRcon = document.getElementById("hide-rcon-noise");
        if (!hideRcon || !hideRcon.checked) {
            return (rawText || "").trim() || "(no logs)";
        }

        const lines = (rawText || "").split("\\n");
        const kept = lines.filter((line) => {
            const lower = line.toLowerCase();
            if (lower.includes("thread rcon client")) return false;
            if (lower.includes("minecraft/rconclient") && lower.includes("shutting down")) return false;
            return true;
        });
        const out = kept.join("\\n").trim();
        return out || "(no logs)";
    }

    function renderMinecraftLog() {
        const target = document.getElementById("minecraft-log");
        if (!target) return;
        target.textContent = filterMinecraftLog(getRawMinecraftLogText());
        scrollLogToBottom();
    }

    function parseCountdown(text) {
        if (!text || text === "--:--") return null;
        const match = text.match(/^([0-9]{2}):([0-9]{2})$/);
        if (!match) return null;
        return (parseInt(match[1], 10) * 60) + parseInt(match[2], 10);
    }

    function formatCountdown(totalSeconds) {
        if (totalSeconds === null) return "--:--";
        const s = Math.max(0, totalSeconds);
        const mins = Math.floor(s / 60).toString().padStart(2, "0");
        const secs = (s % 60).toString().padStart(2, "0");
        return `${mins}:${secs}`;
    }

    function tickIdleCountdown() {
        const idleCountdown = document.getElementById("idle-countdown");
        if (!idleCountdown) return;
        if (idleCountdownSeconds === null) {
            idleCountdown.textContent = "--:--";
            return;
        }
        idleCountdown.textContent = formatCountdown(idleCountdownSeconds);
        if (idleCountdownSeconds > 0) {
            idleCountdownSeconds -= 1;
        }
    }

    async function refreshMinecraftLog() {
        try {
            const response = await fetch("/minecraft-log", { cache: "no-store" });
            if (!response.ok) return;
            const text = await response.text();
            setRawMinecraftLogText(text);
            renderMinecraftLog();
        } catch (err) {
            // Keep current log content on network/read errors.
        }
    }

    function startMinecraftLogStream() {
        if (logEventSource) return;
        logEventSource = new EventSource("/minecraft-log-stream");
        logEventSource.onmessage = (event) => {
            appendRawMinecraftLogLine(event.data || "");
            renderMinecraftLog();
        };
        logEventSource.onerror = () => {
            // EventSource reconnects automatically.
        };
    }

    function stopMinecraftLogStream() {
        if (!logEventSource) return;
        logEventSource.close();
        logEventSource = null;
    }

    function openSudoModal(form) {
        pendingSudoForm = form;
        const modal = document.getElementById("sudo-modal");
        const input = document.getElementById("sudo-modal-input");
        if (!modal || !input) return;
        input.value = "";
        modal.setAttribute("aria-hidden", "false");
        modal.classList.add("open");
        input.focus();
    }

    function closeSudoModal() {
        const modal = document.getElementById("sudo-modal");
        const input = document.getElementById("sudo-modal-input");
        if (modal) {
            modal.classList.remove("open");
            modal.setAttribute("aria-hidden", "true");
            modal.style.display = "none";
            // Force a reflow so next open state is applied cleanly.
            void modal.offsetHeight;
            modal.style.display = "";
        }
        if (input) input.value = "";
        pendingSudoForm = null;
    }

    function showMessageModal(message) {
        // Never stack the rejection modal on top of the password modal.
        closeSudoModal();
        const modal = document.getElementById("message-modal");
        const text = document.getElementById("message-modal-text");
        if (!modal || !text) return;
        text.textContent = message || "";
        modal.setAttribute("aria-hidden", "false");
        modal.classList.add("open");
    }

    function renderCpuPerCore(items) {
        if (!Array.isArray(items) || items.length === 0) {
            return "unknown";
        }
        return items.map((core) => {
            const cls = core.class || "";
            const idx = core.index;
            const val = core.value;
            return `<span class="${cls}">CPU${idx} ${val}%</span>`;
        }).join(" | ");
    }

    async function submitFormAjax(form, options = {}) {
        if (!form) return;
        const action = form.getAttribute("action") || "/";
        const method = (form.getAttribute("method") || "POST").toUpperCase();
        const suppressErrors = options.suppressErrors === true;
        if (action === "/backup") {
            const backupStatus = document.getElementById("backup-status");
            if (backupStatus) {
                backupStatus.textContent = "Running";
                backupStatus.className = "stat-green";
            }
        }
        const formData = new FormData(form);
        if (options.sudoPassword !== undefined) {
            formData.set("sudo_password", options.sudoPassword);
        }
        try {
            const response = await fetch(action, {
                method,
                body: formData,
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json"
                }
            });

            let payload = {};
            try {
                payload = await response.json();
            } catch (e) {
                payload = {};
            }

            if (!response.ok || payload.ok === false) {
                if (!suppressErrors) {
                    showMessageModal(payload.message || "Action rejected.");
                }
                return;
            }

            await refreshMetrics();
        } catch (err) {
            if (!suppressErrors) {
                showMessageModal("Action failed. Please try again.");
            }
        }
    }

    async function refreshMetrics() {
        try {
            const response = await fetch("/metrics", { cache: "no-store" });
            if (!response.ok) return;
            const data = await response.json();
            const ram = document.getElementById("ram-usage");
            const cpu = document.getElementById("cpu-per-core");
            const freq = document.getElementById("cpu-frequency");
            const storage = document.getElementById("storage-usage");
            const players = document.getElementById("players-online");
            const tickRate = document.getElementById("tick-rate");
            const idleCountdown = document.getElementById("idle-countdown");
            const sessionDuration = document.getElementById("session-duration");
            const serviceDurationPrefix = document.getElementById("service-status-duration-prefix");
            const backupStatus = document.getElementById("backup-status");
            const lastBackup = document.getElementById("last-backup-time");
            const nextBackup = document.getElementById("next-backup-time");
            const backupsStatus = document.getElementById("backups-status");
            const service = document.getElementById("service-status");
            const serverTime = document.getElementById("server-time");
            const startBtn = document.getElementById("start-btn");
            const stopBtn = document.getElementById("stop-btn");
            const rconInput = document.getElementById("rcon-command");
            const rconSubmit = document.getElementById("rcon-submit");
            if (ram && data.ram_usage) ram.textContent = data.ram_usage;
            if (cpu && data.cpu_per_core_items) cpu.innerHTML = renderCpuPerCore(data.cpu_per_core_items);
            if (freq && data.cpu_frequency) freq.textContent = data.cpu_frequency;
            if (storage && data.storage_usage) storage.textContent = data.storage_usage;
            if (ram && data.ram_usage_class) ram.className = data.ram_usage_class;
            if (freq && data.cpu_frequency_class) freq.className = data.cpu_frequency_class;
            if (storage && data.storage_usage_class) storage.className = data.storage_usage_class;
            if (players && data.players_online) players.textContent = data.players_online;
            if (tickRate && data.tick_rate !== undefined) tickRate.textContent = data.tick_rate;
            if (data.idle_countdown !== undefined) {
                idleCountdownSeconds = parseCountdown(data.idle_countdown);
                if (idleCountdown) idleCountdown.textContent = data.idle_countdown;
            }
            if (sessionDuration && data.session_duration !== undefined) {
                sessionDuration.textContent = data.session_duration;
            }
            if (backupStatus && data.backup_status) backupStatus.textContent = data.backup_status;
            if (backupStatus && data.backup_status_class) backupStatus.className = data.backup_status_class;
            if (lastBackup && data.last_backup_time) lastBackup.textContent = data.last_backup_time;
            if (nextBackup && data.next_backup_time) nextBackup.textContent = data.next_backup_time;
            if (backupsStatus && data.backups_status) backupsStatus.textContent = data.backups_status;
            if (service && data.service_status) service.textContent = data.service_status;
            if (service && data.service_status_class) service.className = data.service_status_class;
            if (serverTime && data.server_time) serverTime.textContent = data.server_time;
            if (serviceDurationPrefix && service && sessionDuration) {
                if (data.service_status === "Running" && data.session_duration && data.session_duration !== "--") {
                    sessionDuration.style.display = "";
                    serviceDurationPrefix.textContent = " for ";
                } else {
                    sessionDuration.style.display = "none";
                    serviceDurationPrefix.textContent = "";
                }
            }
            if (data.service_running_status === "active") {
                if (startBtn) startBtn.disabled = true;
                if (stopBtn) stopBtn.disabled = false;
                if (rconInput) rconInput.disabled = false;
                if (rconSubmit) rconSubmit.disabled = false;
            } else {
                if (startBtn) startBtn.disabled = false;
                if (stopBtn) stopBtn.disabled = true;
                if (rconInput) rconInput.disabled = true;
                if (rconSubmit) rconSubmit.disabled = true;
            }
            applyRefreshMode(data.service_status);
        } catch (err) {
            // Keep current metrics on network/read errors.
        }
    }

    async function refreshServerStatsOnly() {
        try {
            const response = await fetch("/metrics", { cache: "no-store" });
            if (!response.ok) return;
            const data = await response.json();
            const ram = document.getElementById("ram-usage");
            const cpu = document.getElementById("cpu-per-core");
            const freq = document.getElementById("cpu-frequency");
            const storage = document.getElementById("storage-usage");
            const serverTime = document.getElementById("server-time");
            if (ram && data.ram_usage) ram.textContent = data.ram_usage;
            if (cpu && data.cpu_per_core_items) cpu.innerHTML = renderCpuPerCore(data.cpu_per_core_items);
            if (freq && data.cpu_frequency) freq.textContent = data.cpu_frequency;
            if (storage && data.storage_usage) storage.textContent = data.storage_usage;
            if (serverTime && data.server_time) serverTime.textContent = data.server_time;
            if (ram && data.ram_usage_class) ram.className = data.ram_usage_class;
            if (freq && data.cpu_frequency_class) freq.className = data.cpu_frequency_class;
            if (storage && data.storage_usage_class) storage.className = data.storage_usage_class;
            applyRefreshMode(data.service_status);
        } catch (err) {
            // Keep current server stats on network/read errors.
        }
    }

    function clearRefreshTimers() {
        // Prevent duplicate interval loops when switching modes.
        if (countdownTimer) {
            clearInterval(countdownTimer);
            countdownTimer = null;
        }
        if (metricsTimer) {
            clearInterval(metricsTimer);
            metricsTimer = null;
        }
    }

    function applyRefreshMode(serviceStatusText) {
        // Status is rendered as labels (Off/Starting/Running/Shutting Down).
        const normalized = (serviceStatusText || "").trim().toLowerCase();
        const nextMode = normalized === "off" ? "off" : "active";
        if (nextMode === refreshMode) return;

        refreshMode = nextMode;
        clearRefreshTimers();

        if (refreshMode === "off") {
            // In Off mode, refresh only Server Stats at reduced cadence.
            stopMinecraftLogStream();
            metricsTimer = setInterval(refreshServerStatsOnly, OFF_SERVER_STATS_INTERVAL_MS);
            return;
        }

        // In Active mode, restore full live updates.
        countdownTimer = setInterval(tickIdleCountdown, ACTIVE_COUNTDOWN_INTERVAL_MS);
        metricsTimer = setInterval(refreshMetrics, ACTIVE_METRICS_INTERVAL_MS);
        startMinecraftLogStream();
    }

    window.addEventListener("load", () => {
        document.querySelectorAll("form.ajax-form:not(.sudo-form)").forEach((form) => {
            form.addEventListener("submit", async (event) => {
                event.preventDefault();
                await submitFormAjax(form, {
                    suppressErrors: form.dataset.noRejectModal === "1"
                });
            });
        });

        document.querySelectorAll("form.sudo-form").forEach((form) => {
            form.addEventListener("submit", async (event) => {
                event.preventDefault();
                openSudoModal(form);
            });
        });

        const modalCancel = document.getElementById("sudo-modal-cancel");
        const modalSubmit = document.getElementById("sudo-modal-submit");
        const modalInput = document.getElementById("sudo-modal-input");
        if (modalCancel) {
            modalCancel.addEventListener("click", () => closeSudoModal());
        }
        if (modalSubmit) {
            modalSubmit.addEventListener("click", async () => {
                if (!pendingSudoForm || !modalInput) return;
                const password = (modalInput.value || "").trim();
                if (!password) return;
                const form = pendingSudoForm;
                closeSudoModal();
                await submitFormAjax(form, { sudoPassword: password });
            });
        }
        if (modalInput) {
            modalInput.addEventListener("keydown", (event) => {
                if (event.key === "Enter" && modalSubmit) {
                    event.preventDefault();
                    modalSubmit.click();
                }
            });
        }

        const messageOk = document.getElementById("message-modal-ok");
        if (messageOk) {
            messageOk.addEventListener("click", () => {
                const modal = document.getElementById("message-modal");
                if (modal) modal.classList.remove("open");
            });
        }

        if (alertMessage) {
            showMessageModal(alertMessage);
            const url = new URL(window.location.href);
            url.searchParams.delete("msg");
            window.history.replaceState({}, "", url.pathname + (url.search ? url.search : "") + url.hash);
        }
        scrollLogToBottom();
        const idleCountdown = document.getElementById("idle-countdown");
        if (idleCountdown) {
            idleCountdownSeconds = parseCountdown(idleCountdown.textContent.trim());
        }
        const hideRcon = document.getElementById("hide-rcon-noise");
        if (hideRcon) {
            hideRcon.addEventListener("change", () => {
                renderMinecraftLog();
            });
        }
        const existingLog = document.getElementById("minecraft-log");
        setRawMinecraftLogText(existingLog ? existingLog.textContent : "");
        if (existingLog) {
            renderMinecraftLog();
        }
        const service = document.getElementById("service-status");
        applyRefreshMode(service ? service.textContent : "");
    });
</script>
</body>
</html>
"""

# ----------------------------
# System / privilege helpers
# ----------------------------
def get_status():
    """Return the raw systemd state for the Minecraft service."""
    result = subprocess.run(
        ["systemctl", "is-active", SERVICE],
        capture_output=True, text=True
    )
    return result.stdout.strip()

def set_service_status_intent(intent):
    """Set transient UI status intent: 'starting', 'shutting', or None."""
    global service_status_intent
    with service_status_intent_lock:
        service_status_intent = intent

def get_service_status_intent():
    """Read transient UI status intent."""
    with service_status_intent_lock:
        return service_status_intent

def stop_service_systemd():
    """Attempt to stop the service and verify it is no longer active."""
    # Use only configured sudo-backed command to avoid interactive PolicyKit prompts.
    try:
        run_sudo(["systemctl", "stop", SERVICE])
    except Exception:
        pass

    # Give systemd a short window to transition to inactive/failed.
    deadline = time.time() + 10
    while time.time() < deadline:
        if get_status() in OFF_STATES:
            return True
        time.sleep(0.5)
    return False

def run_sudo(cmd):
    """Run a command with sudo using the configured service password."""
    result = subprocess.run(
        ["sudo", "-S"] + cmd,
        input=f"{SUDO_PASSWORD}\n",
        capture_output=True, text=True
    )
    return result

def validate_sudo_password(sudo_password):
    """Validate user-supplied sudo password for privileged dashboard actions."""
    return (sudo_password or "").strip() == SUDO_PASSWORD

def ensure_session_file():
    """Ensure the session timestamp file exists."""
    try:
        SESSION_FILE.touch(exist_ok=True)
        return True
    except OSError:
        return False

def read_session_start_time():
    """Read session start UNIX timestamp from session file, or None."""
    if not ensure_session_file():
        return None
    try:
        raw = SESSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        ts = float(raw)
    except ValueError:
        return None
    if ts <= 0:
        return None
    # Accept accidental millisecond epoch values.
    if ts > 1_000_000_000_000:
        ts = ts / 1000.0
    return ts

def write_session_start_time(timestamp=None):
    """Persist session start UNIX timestamp to session file."""
    if not ensure_session_file():
        return None
    ts = time.time() if timestamp is None else float(timestamp)
    try:
        SESSION_FILE.write_text(f"{ts:.6f}\n", encoding="utf-8")
    except OSError:
        return None
    return ts

def clear_session_start_time():
    """Clear persisted session start timestamp."""
    if not ensure_session_file():
        return False
    try:
        SESSION_FILE.write_text("", encoding="utf-8")
    except OSError:
        return False
    return True

def get_session_start_time(service_status=None):
    """Return session start time from session.txt when service is not off."""
    if service_status is None:
        service_status = get_status()

    if service_status in OFF_STATES:
        return None
    return read_session_start_time()

def get_session_duration_text(service_status=None):
    """Return elapsed session duration based strictly on session.txt UNIX time."""
    start_time = read_session_start_time()
    if start_time is None:
        return "--"
    # If clock/timestamp is slightly ahead, clamp to zero instead of hiding duration.
    elapsed = max(0, int(time.time() - start_time))
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def get_minecraft_logs_raw():
    """Return raw recent journal lines for client-side filtering/display."""
    result = subprocess.run(
        ["journalctl", "-u", SERVICE, "-n", "500", "--no-pager"],
        capture_output=True,
        text=True,
    )
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    return output or "(no logs)"

# ----------------------------
# Backup status/display helpers
# ----------------------------
def get_backups_status():
    """Return whether the backup directory is present and file count."""
    if not BACKUP_DIR.exists() or not BACKUP_DIR.is_dir():
        return "missing"
    zip_count = sum(1 for _ in BACKUP_DIR.glob("*.zip"))
    return f"ready ({zip_count} zip files)"

def _read_proc_stat():
    """Read CPU stat lines from /proc/stat."""
    with open("/proc/stat", "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.startswith("cpu")]

def _parse_cpu_times(line):
    """Parse total/idle jiffies from one /proc/stat CPU line."""
    parts = line.split()
    values = [int(v) for v in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return total, idle

def get_cpu_usage_per_core():
    """Compute per-core CPU usage by sampling /proc/stat twice."""
    first = _read_proc_stat()
    time.sleep(0.15)
    second = _read_proc_stat()

    usages = []
    for i in range(1, min(len(first), len(second))):
        total1, idle1 = _parse_cpu_times(first[i])
        total2, idle2 = _parse_cpu_times(second[i])
        total_delta = total2 - total1
        idle_delta = idle2 - idle1
        if total_delta <= 0:
            usages.append("0.0")
            continue
        usage = 100.0 * (1.0 - (idle_delta / total_delta))
        usages.append(f"{usage:.1f}")
    return usages

def _class_from_percent(value):
    """Map percentage to severity color class for the dashboard."""
    if value < 60:
        return "stat-green"
    if value < 75:
        return "stat-yellow"
    if value < 90:
        return "stat-orange"
    return "stat-red"

def _extract_percent(usage_text):
    """Extract percent value from strings like '12 / 100 (12.0%)'."""
    match = re.search(r"\(([\d.]+)%\)", usage_text or "")
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None

def get_cpu_per_core_items(cpu_per_core):
    """Return per-core values with independent color classes."""
    # Each core is rendered independently so one hot core does not hide others.
    items = []
    for i, raw in enumerate(cpu_per_core):
        try:
            val = float(raw)
        except ValueError:
            items.append({"index": i, "value": raw, "class": "stat-red"})
            continue
        items.append({"index": i, "value": f"{val:.1f}", "class": _class_from_percent(val)})
    return items

def get_ram_usage_class(ram_usage):
    """Color class based on RAM utilization percentage."""
    percent = _extract_percent(ram_usage)
    if percent is None:
        return "stat-red"
    return _class_from_percent(percent)

def get_storage_usage_class(storage_usage):
    """Color class based on root filesystem utilization percentage."""
    percent = _extract_percent(storage_usage)
    if percent is None:
        return "stat-red"
    return _class_from_percent(percent)

def get_cpu_frequency_class(cpu_frequency):
    """Color class for CPU frequency readout."""
    return "stat-red" if cpu_frequency == "unknown" else "stat-green"

def get_ram_usage():
    """Return RAM usage string based on /proc/meminfo."""
    mem_total_kb = 0
    mem_available_kb = 0
    with open("/proc/meminfo", "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                mem_total_kb = int(line.split()[1])
            elif line.startswith("MemAvailable:"):
                mem_available_kb = int(line.split()[1])

    if mem_total_kb <= 0:
        return "unknown"

    used_kb = mem_total_kb - mem_available_kb
    used_gb = used_kb / (1024 * 1024)
    total_gb = mem_total_kb / (1024 * 1024)
    percent = (used_kb / mem_total_kb) * 100.0
    return f"{used_gb:.2f} / {total_gb:.2f} GB ({percent:.1f}%)"

def get_cpu_frequency():
    """Return average current CPU frequency across cores."""
    freq_paths = sorted(Path("/sys/devices/system/cpu").glob("cpu[0-9]*/cpufreq/scaling_cur_freq"))
    freqs_khz = []
    for path in freq_paths:
        try:
            value = path.read_text(encoding="utf-8").strip()
            freqs_khz.append(int(value))
        except (ValueError, OSError):
            continue

    if freqs_khz:
        avg_ghz = (sum(freqs_khz) / len(freqs_khz)) / 1_000_000
        return f"{avg_ghz:.2f} GHz"

    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            mhz_values = []
            for line in f:
                if line.lower().startswith("cpu mhz"):
                    mhz_values.append(float(line.split(":", 1)[1].strip()))
        if mhz_values:
            avg_ghz = (sum(mhz_values) / len(mhz_values)) / 1000
            return f"{avg_ghz:.2f} GHz"
    except OSError:
        pass

    return "unknown"

def get_storage_usage():
    """Return root filesystem usage from df -h."""
    result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True)
    if result.returncode != 0:
        return "unknown"

    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return "unknown"

    parts = lines[1].split()
    if len(parts) < 6:
        return "unknown"

    used = parts[2]
    size = parts[1]
    percent = parts[4]
    return f"{used} / {size} ({percent})"

def _candidate_mcrcon_bins():
    """Return possible mcrcon executable paths."""
    candidates = []
    found = shutil.which("mcrcon")
    if found:
        candidates.append(found)
    for path in ("/usr/bin/mcrcon", "/usr/local/bin/mcrcon", "/opt/mcrcon/mcrcon"):
        if path not in candidates:
            candidates.append(path)
    return candidates

def _clean_rcon_output(text):
    """Normalize RCON output by removing color/control codes."""
    cleaned = text or ""
    # Strip ANSI escape sequences.
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", cleaned)
    # Strip Minecraft section-formatting codes.
    cleaned = re.sub(r"\u00a7.", "", cleaned)
    return cleaned

def _refresh_rcon_config():
    """Refresh RCON password/port from server.properties when available."""
    global rcon_cached_password
    global rcon_cached_port
    global rcon_last_config_read_at

    now = time.time()
    with rcon_config_lock:
        # Refresh at most once per minute.
        if now - rcon_last_config_read_at < 60:
            return rcon_cached_password, rcon_cached_port

        rcon_last_config_read_at = now
        parsed_password = None
        parsed_port = None

        for path in SERVER_PROPERTIES_CANDIDATES:
            if not path.exists():
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue

            kv = {}
            for raw in lines:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                kv[key.strip()] = value.strip()

            if kv.get("enable-rcon", "").lower() == "false":
                continue

            if kv.get("rcon.password"):
                parsed_password = kv.get("rcon.password")
            if kv.get("rcon.port", "").isdigit():
                parsed_port = int(kv.get("rcon.port"))
            break

        if parsed_password:
            rcon_cached_password = parsed_password
        if parsed_port:
            rcon_cached_port = parsed_port

        return rcon_cached_password, rcon_cached_port

def _run_mcrcon(command, timeout=4):
    """Run one RCON command against local server (with compatibility fallbacks)."""
    password, port = _refresh_rcon_config()

    last_result = None
    for bin_path in _candidate_mcrcon_bins():
        candidates = [
            [bin_path, "-H", RCON_HOST, "-P", str(port), "-p", password, command],
            [bin_path, "-H", RCON_HOST, "-p", password, command],
            [bin_path, "-p", password, command],
        ]
        for argv in candidates:
            try:
                result = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                last_result = result
                if result.returncode == 0:
                    return result
            except Exception as exc:
                continue

    if last_result is not None:
        return last_result
    raise RuntimeError("mcrcon invocation failed")

def _parse_players_online(output):
    """Parse player count from common `list` output variants."""
    text = _clean_rcon_output(output).strip()
    if not text:
        return None

    # Vanilla/Paper format: "There are N of a max of M players online".
    match = re.search(r"There are\s+(\d+)\s+of a max of", text, re.IGNORECASE)
    if match:
        return match.group(1)

    # Some servers return explicit no-player sentence.
    if re.search(r"\bno players online\b", text, re.IGNORECASE):
        return "0"

    # Generic fallback around "players online" phrase.
    match = re.search(r"(\d+)\s+players?\s+online", text, re.IGNORECASE)
    if match:
        return match.group(1)

    # "Players online: N"
    match = re.search(r"Players?\s+online:\s*(\d+)", text, re.IGNORECASE)
    if match:
        return match.group(1)

    return None

def _probe_tick_rate():
    """Probe tick time using multiple command variants and return '<ms> ms' or None."""
    for cmd in ("mspt", "tps", "forge tps", "spark tps"):
        try:
            result = _run_mcrcon(cmd, timeout=8)
        except Exception:
            continue

        if result.returncode != 0:
            continue

        output = _clean_rcon_output((result.stdout or "") + (result.stderr or "")).strip()
        if not output:
            continue
        cleaned = output

        # Prefer direct mspt/ms values when available.
        ms_match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*ms", cleaned, re.IGNORECASE)
        if ms_match:
            try:
                ms_val = float(ms_match.group(1).replace(",", "."))
                if ms_val > 0:
                    return f"{ms_val:.1f} ms"
            except ValueError:
                pass

        # Convert explicit TPS values to ms/tick.
        match = re.search(r"TPS[^0-9]*([0-9]+(?:[.,][0-9]+)?)", cleaned, re.IGNORECASE)
        if match:
            try:
                tps = float(match.group(1).replace(",", "."))
                if tps > 0:
                    return f"{(1000.0 / tps):.1f} ms"
            except ValueError:
                pass

        # Fallback: parse first numeric token (guarded to plausible TPS range).
        match = re.search(r"\b([0-9]+(?:[.,][0-9]+)?)\b", cleaned)
        if match:
            try:
                tps = float(match.group(1).replace(",", "."))
                if 0 < tps <= 30:
                    return f"{(1000.0 / tps):.1f} ms"
            except ValueError:
                pass

    return None

def _probe_minecraft_runtime_metrics(force=False):
    """Return cached/updated (players_online, tick_rate) values."""
    global mc_last_query_at
    global mc_cached_players_online
    global mc_cached_tick_rate

    # Fast path: skip probing when service is down.
    if get_status() != "active":
        with mc_query_lock:
            mc_cached_players_online = "unknown"
            mc_cached_tick_rate = "unknown"
        return "unknown", "unknown"

    now = time.time()
    with mc_query_lock:
        if not force and (now - mc_last_query_at) < MC_QUERY_INTERVAL_SECONDS:
            return mc_cached_players_online, mc_cached_tick_rate

    players_value = None
    tick_value = None

    try:
        result = _run_mcrcon("list", timeout=8)
        if result.returncode == 0:
            combined = (result.stdout or "") + (result.stderr or "")
            players_value = _parse_players_online(combined)
    except Exception:
        players_value = None

    try:
        tick_value = _probe_tick_rate()
    except Exception:
        tick_value = None

    with mc_query_lock:
        # Keep last known values on transient RCON failures while service is active.
        if players_value is not None:
            mc_cached_players_online = players_value
        elif mc_cached_players_online == "unknown":
            mc_cached_players_online = "unknown"

        if tick_value is not None:
            mc_cached_tick_rate = tick_value
        elif mc_cached_tick_rate == "unknown":
            mc_cached_tick_rate = "unknown"

        mc_last_query_at = now
        return mc_cached_players_online, mc_cached_tick_rate

def get_players_online():
    """Return online player count from cached RCON probe."""
    players_online, _ = _probe_minecraft_runtime_metrics()
    return players_online

def get_tick_rate():
    """Return server tick time from cached RCON probe."""
    _, tick_rate = _probe_minecraft_runtime_metrics()
    return tick_rate

def get_service_status_display(service_status, players_online):
    """Map raw service + start/stop intent into rule-based UI status labels."""
    # Rule 1: Off when systemd says service is off.
    if service_status in ("inactive", "failed"):
        set_service_status_intent(None)
        return "Off"

    # Transitional systemd states keep obvious lifecycle labels.
    if service_status == "activating":
        return "Starting"
    if service_status == "deactivating":
        return "Shutting Down"

    # Active state: apply user-requested rules based on players + trigger intent.
    if service_status == "active":
        players_is_integer = isinstance(players_online, str) and players_online.isdigit()
        intent = get_service_status_intent()

        # Rule 2: Running when systemd active and players is an integer.
        if players_is_integer:
            # Once players become resolvable, startup/shutdown transient intent is done.
            if intent in ("starting", "shutting"):
                set_service_status_intent(None)
            return "Running"

        # Rule 3 and 4: unknown players + trigger intent.
        if intent == "shutting":
            return "Shutting Down"
        # Default unknown-on-active and explicit start intent both map to Starting.
        return "Starting"

    return "Off"

def get_service_status_class(service_status_display):
    """Map display status to UI severity color class."""
    if service_status_display == "Running":
        return "stat-green"
    if service_status_display == "Starting":
        return "stat-yellow"
    if service_status_display == "Shutting Down":
        return "stat-orange"
    return "stat-red"

def graceful_stop_minecraft():
    """Stop sequence: systemd stop -> backup."""
    # Run steps in strict order, regardless of intermediate failures.
    systemd_ok = stop_service_systemd()
    backup_ok = run_backup_script()
    return {
        "systemd_ok": systemd_ok,
        "backup_ok": backup_ok,
    }

def stop_server_automatically():
    """Gracefully stop Minecraft (used by idle watcher)."""
    set_service_status_intent("shutting")
    graceful_stop_minecraft()
    clear_session_start_time()
    reset_backup_periodic_runs()

def run_backup_script():
    """Run backup script and update in-memory backup status."""
    global backup_active_jobs
    global backup_last_error
    global backup_last_successful_at

    with backup_lock:
        # Track active backup jobs for UI status.
        backup_active_jobs += 1
        backup_last_error = ""

    success = False
    before_snapshot = get_backup_zip_snapshot()
    try:
        # Try direct execution first; some setups succeed even if script emits
        # non-zero due to auxiliary commands (e.g., mcrcon syntax mismatch).
        direct_result = subprocess.run(
            [BACKUP_SCRIPT],
            capture_output=True,
            text=True,
            timeout=600,
        )
        after_direct_snapshot = get_backup_zip_snapshot()
        direct_created_zip = backup_snapshot_changed(before_snapshot, after_direct_snapshot)

        if direct_result.returncode == 0 or direct_created_zip:
            success = True
            with backup_lock:
                backup_last_successful_at = time.time()
        else:
            # Fallback to sudo-backed execution when direct run truly failed.
            sudo_result = run_sudo([BACKUP_SCRIPT])
            after_sudo_snapshot = get_backup_zip_snapshot()
            sudo_created_zip = backup_snapshot_changed(before_snapshot, after_sudo_snapshot)
            success = sudo_result.returncode == 0 or sudo_created_zip
            if success:
                with backup_lock:
                    backup_last_successful_at = time.time()

            if not success:
                err = (
                    (sudo_result.stderr or "")
                    + "\n"
                    + (sudo_result.stdout or "")
                    + "\n"
                    + (direct_result.stderr or "")
                    + "\n"
                    + (direct_result.stdout or "")
                ).strip()
                with backup_lock:
                    backup_last_error = err[:700] if err else "Backup command returned non-zero exit status."
    finally:
        with backup_lock:
            backup_active_jobs = max(0, backup_active_jobs - 1)

    return success

def format_backup_time(timestamp):
    """Format UNIX timestamp for the dashboard or return '--'."""
    if timestamp is None:
        return "--"
    return datetime.fromtimestamp(timestamp, tz=DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z")

def get_server_time_text():
    """Return current server time for header display."""
    return datetime.now(tz=DISPLAY_TZ).strftime("%b %d, %Y %I:%M:%S %p %Z")

def get_latest_backup_zip_timestamp():
    """Return mtime of newest ZIP backup file, if available."""
    if not BACKUP_DIR.exists() or not BACKUP_DIR.is_dir():
        return None
    latest = None
    for path in BACKUP_DIR.glob("*.zip"):
        try:
            ts = path.stat().st_mtime
        except OSError:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest

def get_backup_zip_snapshot():
    """Return snapshot of zip files as {path: mtime_ns} for change detection."""
    snapshot = {}
    if not BACKUP_DIR.exists() or not BACKUP_DIR.is_dir():
        return snapshot
    for path in BACKUP_DIR.glob("*.zip"):
        try:
            snapshot[str(path)] = path.stat().st_mtime_ns
        except OSError:
            continue
    return snapshot

def backup_snapshot_changed(before_snapshot, after_snapshot):
    """Return True when backup artifacts changed (new file or updated mtime)."""
    if not before_snapshot and after_snapshot:
        return True
    for file_path, after_mtime in after_snapshot.items():
        before_mtime = before_snapshot.get(file_path)
        if before_mtime is None:
            return True
        if after_mtime != before_mtime:
            return True
    return False

def get_backup_schedule_times(service_status=None):
    """Return last/next backup timestamps for dashboard display."""
    global backup_last_successful_at

    if service_status is None:
        service_status = get_status()

    latest_zip_ts = get_latest_backup_zip_timestamp()
    with backup_lock:
        last_success_ts = backup_last_successful_at
    if latest_zip_ts is None:
        last_backup_ts = last_success_ts
    elif last_success_ts is None:
        last_backup_ts = latest_zip_ts
    else:
        last_backup_ts = max(latest_zip_ts, last_success_ts)

    next_backup_at = None
    if service_status not in OFF_STATES:
        # Next backup is aligned to fixed interval boundaries from session start.
        anchor = get_session_start_time(service_status)
        if anchor is not None:
            elapsed_intervals = int(max(0, time.time() - anchor) // BACKUP_INTERVAL_SECONDS)
            next_backup_at = anchor + ((elapsed_intervals + 1) * BACKUP_INTERVAL_SECONDS)

    return {
        "last_backup_time": format_backup_time(last_backup_ts),
        "next_backup_time": format_backup_time(next_backup_at),
    }

def get_backup_status(latest_backup_snapshot=None):
    """Return dashboard backup activity status label and color class."""
    global backup_waiting_for_last_change
    global backup_waiting_baseline_snapshot

    if latest_backup_snapshot is None:
        latest_backup_snapshot = get_backup_zip_snapshot()

    with backup_lock:
        active = backup_active_jobs > 0
        waiting = backup_waiting_for_last_change
        baseline_snapshot = backup_waiting_baseline_snapshot or {}

    if waiting:
        # Stay in Running state until the "last backup" source value changes.
        if backup_snapshot_changed(baseline_snapshot, latest_backup_snapshot):
            with backup_lock:
                backup_waiting_for_last_change = False
                backup_waiting_baseline_snapshot = {}
            waiting = False
        else:
            return "Running", "stat-green"

    if active:
        return "Running", "stat-green"
    return "Idle", "stat-yellow"

def reset_backup_periodic_runs():
    """Reset periodic backup run counter."""
    global backup_periodic_runs
    with backup_lock:
        backup_periodic_runs = 0

def collect_dashboard_metrics():
    """Collect shared dashboard metrics for both HTML and JSON responses."""
    cpu_per_core = get_cpu_usage_per_core()
    ram_usage = get_ram_usage()
    cpu_frequency = get_cpu_frequency()
    storage_usage = get_storage_usage()
    service_status = get_status()
    players_online = get_players_online()
    tick_rate = get_tick_rate()
    session_duration = get_session_duration_text(service_status)
    service_status_display = get_service_status_display(service_status, players_online)
    backup_schedule = get_backup_schedule_times(service_status)
    latest_backup_snapshot = get_backup_zip_snapshot()
    backup_status, backup_status_class = get_backup_status(latest_backup_snapshot)

    return {
        "service_status": service_status_display,
        "service_status_class": get_service_status_class(service_status_display),
        "service_running_status": service_status,
        "backups_status": get_backups_status(),
        "ram_usage": ram_usage,
        "ram_usage_class": get_ram_usage_class(ram_usage),
        "cpu_per_core_items": get_cpu_per_core_items(cpu_per_core),
        "cpu_frequency": cpu_frequency,
        "cpu_frequency_class": get_cpu_frequency_class(cpu_frequency),
        "storage_usage": storage_usage,
        "storage_usage_class": get_storage_usage_class(storage_usage),
        "players_online": players_online,
        "tick_rate": tick_rate,
        "session_duration": session_duration,
        "idle_countdown": get_idle_countdown(service_status, players_online),
        "backup_status": backup_status,
        "backup_status_class": backup_status_class,
        "last_backup_time": backup_schedule["last_backup_time"],
        "next_backup_time": backup_schedule["next_backup_time"],
        "server_time": get_server_time_text(),
    }

def format_countdown(seconds):
    """Render remaining seconds as MM:SS."""
    if seconds <= 0:
        return "00:00"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins:02d}:{secs:02d}"

def get_idle_countdown(service_status=None, players_online=None):
    """Return idle auto-shutdown countdown string for UI."""
    if service_status is None:
        service_status = get_status()
    if players_online is None:
        players_online = get_players_online()

    if service_status != "active" or players_online != "0":
        return "--:--"

    with idle_lock:
        if idle_zero_players_since is None:
            return format_countdown(IDLE_ZERO_PLAYERS_SECONDS)
        elapsed = time.time() - idle_zero_players_since

    remaining = IDLE_ZERO_PLAYERS_SECONDS - elapsed
    return format_countdown(remaining)

def idle_player_watcher():
    """Background loop: stop server after sustained zero-player idle time."""
    global idle_zero_players_since

    while True:
        try:
            service_status = get_status()
            players_online = get_players_online()
            now = time.time()

            with idle_lock:
                # Count only continuous periods where the server is up and empty.
                if service_status == "active" and players_online == "0":
                    if idle_zero_players_since is None:
                        idle_zero_players_since = now
                    elif now - idle_zero_players_since >= IDLE_ZERO_PLAYERS_SECONDS:
                        stop_server_automatically()
                        idle_zero_players_since = None
                else:
                    idle_zero_players_since = None
        except Exception:
            # Keep watcher alive on transient command failures.
            pass

        time.sleep(IDLE_CHECK_INTERVAL_SECONDS)

def start_idle_player_watcher():
    """Start idle watcher in a daemon thread."""
    watcher = threading.Thread(target=idle_player_watcher, daemon=True)
    watcher.start()

def backup_session_watcher():
    """Background loop: periodic backups during active sessions.

    If a session ends before reaching the backup interval, run one backup at
    shutdown so short sessions still produce a backup artifact.
    """
    global backup_periodic_runs

    while True:
        try:
            now = time.time()
            service_status = get_status()
            is_running = service_status == "active"
            is_off = service_status in ("inactive", "failed")

            should_run_periodic_backup = False
            should_run_shutdown_backup = False

            session_started_at = read_session_start_time()

            with backup_lock:
                if is_running:
                    # Session anchor is persisted in session.txt.
                    if session_started_at is None:
                        # No valid session anchor: skip schedule calculation.
                        # Start button/init logic is responsible for setting it.
                        pass

                    # Number of interval boundaries crossed since session start.
                    if session_started_at is not None:
                        due_runs = int((now - session_started_at) // BACKUP_INTERVAL_SECONDS)
                        if due_runs > backup_periodic_runs:
                            should_run_periodic_backup = True
                elif is_off and session_started_at is not None:
                    # Session ended (truly off): always run one final backup.
                    # Do not clear during transitional states like activating/deactivating.
                    should_run_shutdown_backup = True

                    clear_session_start_time()
                    backup_periodic_runs = 0

            if should_run_periodic_backup:
                # Keep schedule counters in sync only when backup succeeds.
                if run_backup_script():
                    with backup_lock:
                        backup_periodic_runs += 1

            if should_run_shutdown_backup:
                run_backup_script()
        except Exception:
            # Keep watcher alive on transient command failures.
            pass

        time.sleep(15)

def start_backup_session_watcher():
    """Start backup scheduler in a daemon thread."""
    watcher = threading.Thread(target=backup_session_watcher, daemon=True)
    watcher.start()

def initialize_session_tracking():
    """Initialize session.txt on process boot with session-preserving rules."""
    ensure_session_file()
    service_status = get_status()
    session_start = read_session_start_time()

    # If server is off, clear session start.
    if service_status in OFF_STATES:
        clear_session_start_time()
        return

    # Server is up/transitional: keep existing session anchor if present.
    # Only seed with current time when file is empty/invalid.
    if session_start is None:
        write_session_start_time()

def _status_debug_note():
    """Return quick status note for troubleshooting session tracking."""
    try:
        service_status = get_status()
        session_raw = ""
        if ensure_session_file():
            session_raw = SESSION_FILE.read_text(encoding="utf-8").strip()
        return f"service={service_status}, session_file={'<empty>' if not session_raw else session_raw}"
    except Exception:
        return "service=unknown, session_file=unreadable"

def _session_write_failed_response():
    """Uniform response when session file cannot be written."""
    message = "Session file write failed."
    if _is_ajax_request():
        return jsonify({"ok": False, "error": "session_write_failed", "message": f"{message} {_status_debug_note()}"}), 500
    return redirect("/?msg=session_write_failed")

def _ensure_session_anchor_for_start_or_fail():
    """Set session start for explicit start action and validate persistence."""
    ts = write_session_start_time()
    return ts is not None

def _ensure_session_clear_for_stop_or_fail():
    """Clear session file after stop and validate persistence."""
    return clear_session_start_time()

def ensure_session_tracking_initialized():
    """Run session tracking initialization once per process."""
    global session_tracking_initialized
    if session_tracking_initialized:
        return
    with session_tracking_lock:
        if session_tracking_initialized:
            return
        initialize_session_tracking()
        session_tracking_initialized = True

@app.before_request
def _initialize_session_tracking_before_request():
    """Ensure session tracking is initialized even under WSGI launch."""
    ensure_session_tracking_initialized()

# Eagerly initialize at import/startup so session.txt is reconciled immediately,
# even before the first HTTP request is received.
ensure_session_tracking_initialized()

def _is_ajax_request():
    """Return True when request expects JSON response (fetch/XHR)."""
    # Primary AJAX signal used by fetch requests from this UI.
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    # Fallback signal when only Accept header is provided.
    accept = request.headers.get("Accept", "")
    return "application/json" in accept.lower()

def _ok_response():
    """Return appropriate success response for ajax/non-ajax requests."""
    # AJAX callers need JSON, while legacy form submissions expect redirect.
    if _is_ajax_request():
        return jsonify({"ok": True})
    return redirect("/")

def _password_rejected_response():
    """Return password rejection response for ajax/non-ajax requests."""
    # Keep one shared password-rejected payload/message for consistency.
    if _is_ajax_request():
        return jsonify({
            "ok": False,
            "error": "password_incorrect",
            "message": "Password incorrect. Action rejected.",
        }), 403
    return redirect("/?msg=password_incorrect")

def _backup_failed_response(message):
    """Return backup failure response for ajax/non-ajax requests."""
    if _is_ajax_request():
        return jsonify({"ok": False, "error": "backup_failed", "message": message}), 500
    return redirect("/?msg=backup_failed")

#
# ----------------------------
# Flask routes
# ----------------------------
@app.route("/")
def index():
    """Render dashboard page."""
    # Legacy query-parameter path (kept for non-AJAX fallback flows).
    message_code = request.args.get("msg", "")
    alert_message = ""
    if message_code == "password_incorrect":
        alert_message = "Password incorrect. Action rejected."
    elif message_code == "session_write_failed":
        alert_message = "Session file write failed."
    elif message_code == "backup_failed":
        alert_message = "Backup failed."

    data = collect_dashboard_metrics()
    return render_template_string(
        HTML,
        service_status=data["service_status"],
        service_status_class=data["service_status_class"],
        service_running_status=data["service_running_status"],
        backups_status=data["backups_status"],
        cpu_per_core_items=data["cpu_per_core_items"],
        cpu_frequency=data["cpu_frequency"],
        cpu_frequency_class=data["cpu_frequency_class"],
        storage_usage=data["storage_usage"],
        storage_usage_class=data["storage_usage_class"],
        players_online=data["players_online"],
        tick_rate=data["tick_rate"],
        session_duration=data["session_duration"],
        idle_countdown=data["idle_countdown"],
        backup_status=data["backup_status"],
        backup_status_class=data["backup_status_class"],
        last_backup_time=data["last_backup_time"],
        next_backup_time=data["next_backup_time"],
        server_time=data["server_time"],
        ram_usage=data["ram_usage"],
        ram_usage_class=data["ram_usage_class"],
        minecraft_logs_raw=get_minecraft_logs_raw(),
        alert_message=alert_message,
    )

@app.route("/minecraft-log")
def minecraft_log():
    """Return plain-text Minecraft service log snippet."""
    return get_minecraft_logs_raw(), 200, {"Content-Type": "text/plain; charset=utf-8"}

@app.route("/minecraft-log-stream")
def minecraft_log_stream():
    """Stream new Minecraft journal lines via SSE."""
    def generate():
        proc = None
        try:
            proc = subprocess.Popen(
                ["journalctl", "-u", SERVICE, "-f", "-n", "0", "--no-pager"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            if not proc.stdout:
                return

            for line in proc.stdout:
                clean = line.rstrip("\r\n")
                if not clean:
                    continue
                # SSE payload: one new journal line per message.
                yield f"data: {clean}\n\n"
        except GeneratorExit:
            pass
        except Exception:
            yield "event: error\ndata: stream_error\n\n"
        finally:
            if proc and proc.poll() is None:
                proc.terminate()

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

@app.route("/metrics")
def metrics():
    """Return dynamic dashboard metrics as JSON."""
    # This endpoint is polled by the UI; keep payload compact and explicit.
    return jsonify(collect_dashboard_metrics())

@app.route("/start", methods=["POST"])
def start():
    """Start Minecraft service and initialize backup session state."""
    set_service_status_intent("starting")
    # Start via systemd so status/auto-watchers remain aligned to one source.
    # Start service using systemd as source-of-truth for process lifecycle.
    subprocess.run(["sudo", "systemctl", "start", SERVICE])
    if not _ensure_session_anchor_for_start_or_fail():
        return _session_write_failed_response()
    reset_backup_periodic_runs()
    return _ok_response()

@app.route("/stop", methods=["POST"])
def stop():
    """Stop Minecraft service using user-supplied sudo password."""
    sudo_password = request.form.get("sudo_password", "")
    if not validate_sudo_password(sudo_password):
        return _password_rejected_response()

    set_service_status_intent("shutting")
    # Ordered shutdown path:
    # 1) RCON stop, 2) final backup, 3) systemd stop (inside helper).
    # Executes ordered shutdown (RCON stop -> backup -> systemd stop).
    graceful_stop_minecraft()
    _ensure_session_clear_for_stop_or_fail()
    reset_backup_periodic_runs()
    return _ok_response()

@app.route("/backup", methods=["POST"])
def backup():
    """Run backup script manually from dashboard."""
    global backup_waiting_for_last_change
    global backup_waiting_baseline_snapshot

    with backup_lock:
        backup_waiting_for_last_change = True
        backup_waiting_baseline_snapshot = get_backup_zip_snapshot()

    # Manual backup should not shift the periodic schedule anchor.
    if not run_backup_script():
        detail = ""
        with backup_lock:
            detail = backup_last_error
        message = "Backup failed."
        if detail:
            message = f"Backup failed: {detail}"
        return _backup_failed_response(message)
    return _ok_response()

@app.route("/rcon", methods=["POST"])
def rcon():
    """Execute an RCON command after validating sudo password."""
    command = request.form.get("rcon_command", "").strip()
    sudo_password = request.form.get("sudo_password", "")
    if not command:
        if _is_ajax_request():
            return jsonify({"ok": False, "message": "Command is required."}), 400
        return redirect("/")
    # Block command execution when service is not active.
    if get_status() != "active":
        if _is_ajax_request():
            return jsonify({"ok": False, "message": "Server is not running."}), 409
        return redirect("/")
    if not validate_sudo_password(sudo_password):
        return _password_rejected_response()

    # Execute through the shared RCON runner so host/port/password fallbacks apply.
    try:
        result = _run_mcrcon(command, timeout=8)
    except Exception:
        if _is_ajax_request():
            return jsonify({"ok": False, "message": "RCON command failed to execute."}), 500
        return redirect("/")

    if result.returncode != 0:
        detail = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
        message = "RCON command failed."
        if detail:
            message = f"RCON command failed: {detail[:400]}"
        if _is_ajax_request():
            return jsonify({"ok": False, "message": message}), 500
        return redirect("/")

    return _ok_response()

if __name__ == "__main__":
    # Start background automation loops before serving HTTP requests.
    ensure_session_tracking_initialized()
    start_idle_player_watcher()
    start_backup_session_watcher()
    app.run(host="0.0.0.0", port=8080)
