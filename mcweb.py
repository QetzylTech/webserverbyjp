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

app = Flask(__name__)

# Core service/application settings.
SERVICE = "minecraft"
BACKUP_SCRIPT = "/opt/Minecraft/backup.sh"
BACKUP_DIR = Path("/home/marites/backups")
SESSION_FILE = Path(__file__).resolve().parent / "session.txt"
SUDO_PASSWORD = "SuperCute"
RCON_PASSWORD = "SuperCute"

# Backup/automation timing controls.
BACKUP_INTERVAL_HOURS = 6
BACKUP_INTERVAL_SECONDS = max(60, int(BACKUP_INTERVAL_HOURS * 3600))
IDLE_ZERO_PLAYERS_SECONDS = 180
IDLE_CHECK_INTERVAL_SECONDS = 15

# Shared watcher state (protected by locks below).
idle_zero_players_since = None
idle_lock = threading.Lock()
backup_last_run_at = None
backup_last_completed_at = None
backup_had_periodic_run = False
backup_periodic_runs = 0
backup_lock = threading.Lock()
backup_active_jobs = 0
backup_last_started_at = None
backup_last_finished_at = None
backup_last_success = None
session_tracking_initialized = False
session_tracking_lock = threading.Lock()

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

    .panel-controls input[type="text"] {
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
                    <form class="ajax-form" method="post" action="/start">
                        <button id="start-btn" type="submit" {% if service_running_status == "active" %}disabled{% endif %}>Start</button>
                    </form>
                    <form class="ajax-form sudo-form" method="post" action="/stop">
                        <input type="hidden" name="sudo_password">
                        <button id="stop-btn" class="btn-stop" type="submit" {% if service_running_status != "active" %}disabled{% endif %}>Stop</button>
                    </form>
                    <form class="ajax-form" method="post" action="/backup" data-no-reject-modal="1">
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
                        <span>Server Status: <b id="service-status" class="{{ service_status_class }}">{{ service_status }}</b></span>
                        <span>Players online: <b id="players-online">{{ players_online }}</b></span>
                        <span>Tick time: <b id="tick-rate">{{ tick_rate }}</b></span>
                        <span>Auto-stop in: <b id="idle-countdown">{{ idle_countdown }}</b></span>
                        <span>Session duration: <b id="session-duration">{{ session_duration }}</b></span>
                    </div>
                </div>
                <!-- Backup scheduler/activity metrics. -->
                <div class="stats-group">
                    <p class="group-title">Backup Stats</p>
                    <div class="status-row">
                        <span>Backup status: <b id="backup-status" class="{{ backup_status_class }}">{{ backup_status }}</b></span>
                        <span>Last backup: <b id="last-backup-time">{{ last_backup_time }}</b></span>
                        <span>Next backup: <b id="next-backup-time">{{ next_backup_time }}</b></span>
                        <span>Backups folder: <b>{{ backups_status }}</b></span>
                    </div>
                </div>
            </div>
        </div>

    </section>

    <!-- Main content: filtered Minecraft service logs. -->
    <section class="logs">
        <article class="panel">
            <div class="panel-header">
                <h3>Minecraft Log (last 50 lines)</h3>
                <form class="panel-controls ajax-form sudo-form" method="post" action="/rcon">
                    <label class="panel-filter">
                        <input id="hide-rcon-noise" type="checkbox" checked>
                        Hide RCON noise
                    </label>
                    <input type="hidden" name="sudo_password">
                    <input id="rcon-command" type="text" name="rcon_command" placeholder="Enter Minecraft server command (e.g., say hello)" {% if service_running_status != "active" %}disabled{% endif %} required>
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
        if (rawMinecraftLogLines.length > 3000) {
            rawMinecraftLogLines = rawMinecraftLogLines.slice(-3000);
        }
    }

    function appendRawMinecraftLogLine(line) {
        rawMinecraftLogLines.push(line || "");
        if (rawMinecraftLogLines.length > 3000) {
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
            const backupStatus = document.getElementById("backup-status");
            const lastBackup = document.getElementById("last-backup-time");
            const nextBackup = document.getElementById("next-backup-time");
            const service = document.getElementById("service-status");
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
            if (service && data.service_status) service.textContent = data.service_status;
            if (service && data.service_status_class) service.className = data.service_status_class;
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
            if (ram && data.ram_usage) ram.textContent = data.ram_usage;
            if (cpu && data.cpu_per_core_items) cpu.innerHTML = renderCpuPerCore(data.cpu_per_core_items);
            if (freq && data.cpu_frequency) freq.textContent = data.cpu_frequency;
            if (storage && data.storage_usage) storage.textContent = data.storage_usage;
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
    except OSError:
        pass

def read_session_start_time():
    """Read session start UNIX timestamp from session file, or None."""
    ensure_session_file()
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
    return ts if ts > 0 else None

def write_session_start_time(timestamp=None):
    """Persist session start UNIX timestamp to session file."""
    ensure_session_file()
    ts = time.time() if timestamp is None else float(timestamp)
    try:
        SESSION_FILE.write_text(f"{ts:.6f}\n", encoding="utf-8")
    except OSError:
        pass
    return ts

def clear_session_start_time():
    """Clear persisted session start timestamp."""
    ensure_session_file()
    try:
        SESSION_FILE.write_text("", encoding="utf-8")
    except OSError:
        pass

def get_session_start_time(service_status=None):
    """Return current session start time from session file when applicable."""
    if service_status is None:
        service_status = get_status()

    session_start = read_session_start_time()
    if service_status != "active":
        return None
    if session_start is not None:
        return session_start
    # Fallback so schedule/duration still work if file was empty while running.
    return write_session_start_time()

def get_session_duration_text(service_status=None):
    """Return elapsed session duration based on session.txt anchor."""
    if service_status is None:
        service_status = get_status()
    if service_status != "active":
        return "--"

    start_time = get_session_start_time(service_status)
    if start_time is None:
        return "--"

    elapsed = max(0, int(time.time() - start_time))
    hours = elapsed // 3600
    minutes = (elapsed % 3600) // 60
    seconds = elapsed % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

def get_minecraft_logs_raw():
    """Return raw recent journal lines for client-side filtering/display."""
    result = run_sudo(["journalctl", "-u", SERVICE, "-n", "400", "--no-pager"])
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

def format_cpu_per_core_text(cpu_per_core):
    """Format per-core percentages for compact header display."""
    if not cpu_per_core:
        return "unknown"
    # return " | ".join([f"Core{i} {val}%" for i, val in enumerate(cpu_per_core)])
    return " | ".join([f"{val}%" for i, val in enumerate(cpu_per_core)])

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

def get_cpu_usage_class(cpu_per_core):
    """Color class based on hottest CPU core usage."""
    if not cpu_per_core:
        return "stat-red"
    try:
        peak = max(float(v) for v in cpu_per_core)
    except ValueError:
        return "stat-red"
    return _class_from_percent(peak)

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

def get_players_online():
    """Return online player count from mcrcon, or 'unknown'."""
    result = subprocess.run(
        ["mcrcon", "-p", RCON_PASSWORD, "list"],
        capture_output=True,
        text=True,
        timeout=4,
    )
    if result.returncode != 0:
        return "unknown"

    output = (result.stdout or "") + (result.stderr or "")
    marker = "There are "
    idx = output.find(marker)
    if idx == -1:
        return "unknown"

    fragment = output[idx + len(marker):].strip()
    count = fragment.split(" ", 1)[0]
    return count if count.isdigit() else "unknown"

def get_tick_rate():
    """Return server tick time in milliseconds per tick when available."""
    # Skip probing when systemd already reports server as not active.
    if get_status() != "active":
        return "unknown"

    # Try commands commonly available across Paper/Spigot/modded stacks.
    for cmd in ("mspt", "tps", "forge tps", "spark tps"):
        try:
            result = subprocess.run(
                ["mcrcon", "-p", RCON_PASSWORD, cmd],
                capture_output=True,
                text=True,
                timeout=4,
            )
        except Exception:
            continue

        if result.returncode != 0:
            continue

        output = ((result.stdout or "") + (result.stderr or "")).strip()
        if not output:
            continue

        # Strip Minecraft section-formatting codes if present.
        cleaned = re.sub(r"\u00a7.", "", output)

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
                # Guard against unrelated numbers in output.
                if 0 < tps <= 30:
                    return f"{(1000.0 / tps):.1f} ms"
            except ValueError:
                pass

    return "unknown"

def get_service_status_display(service_status, players_online):
    """Map raw service + RCON readiness into UI-friendly status labels."""
    # Prefer systemd lifecycle for coarse state, use RCON readiness as a hint.
    if service_status in ("inactive", "failed"):
        return "Off"
    if service_status == "activating":
        return "Starting"
    if service_status == "deactivating":
        return "Shutting Down"
    if service_status == "active":
        if players_online == "unknown":
            return "Starting"
        return "Running"
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
    """Gracefully stop via RCON, run final backup, then stop systemd unit."""
    # Order matters:
    # 1) Ask Minecraft to stop cleanly (saves world)
    # 2) Take final backup
    # 3) Ensure unit is down at the service manager level
    subprocess.run(
        ["mcrcon", "-p", RCON_PASSWORD, "stop"],
        capture_output=True,
        text=True,
        timeout=8,
    )
    run_backup_script(track_session_schedule=False)
    run_sudo(["systemctl", "stop", SERVICE])

def stop_server_automatically():
    """Gracefully stop Minecraft (used by idle watcher)."""
    global backup_had_periodic_run
    global backup_periodic_runs

    graceful_stop_minecraft()
    clear_session_start_time()
    with backup_lock:
        backup_last_run_at = None
        backup_had_periodic_run = False
        backup_periodic_runs = 0

def run_backup_script(track_session_schedule=True):
    """Run backup script and update in-memory backup timestamps."""
    global backup_last_run_at
    global backup_last_completed_at
    global backup_active_jobs
    global backup_last_started_at
    global backup_last_finished_at
    global backup_last_success

    with backup_lock:
        # Track active backup jobs for UI status.
        backup_active_jobs += 1
        backup_last_started_at = time.time()
        backup_last_success = None

    success = False
    try:
        success = run_sudo([BACKUP_SCRIPT]).returncode == 0
        if success:
            now = time.time()
            with backup_lock:
                backup_last_completed_at = now
                if track_session_schedule:
                    backup_last_run_at = now
    finally:
        with backup_lock:
            backup_active_jobs = max(0, backup_active_jobs - 1)
            backup_last_finished_at = time.time()
            backup_last_success = success

    return success

def format_backup_time(timestamp):
    """Format UNIX timestamp for the dashboard or return '--'."""
    if timestamp is None:
        return "--"
    return datetime.fromtimestamp(timestamp).strftime("%b %d, %Y %I:%M:%S %p")

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

def get_backup_schedule_times(service_status=None):
    """Return last/next backup timestamps for dashboard display."""
    if service_status is None:
        service_status = get_status()

    next_backup_at = None
    if service_status == "active":
        # Next backup is aligned to fixed interval boundaries from session start.
        anchor = get_session_start_time(service_status)
        if anchor is None:
            anchor = time.time()
        elapsed_intervals = int(max(0, time.time() - anchor) // BACKUP_INTERVAL_SECONDS)
        next_backup_at = anchor + ((elapsed_intervals + 1) * BACKUP_INTERVAL_SECONDS)

    return {
        "last_backup_time": format_backup_time(get_latest_backup_zip_timestamp()),
        "next_backup_time": format_backup_time(next_backup_at),
    }

def get_backup_status():
    """Return dashboard backup activity status label and color class."""
    with backup_lock:
        active = backup_active_jobs > 0

    if active:
        return "Running", "stat-green"
    return "Idle", "stat-yellow"

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
    global backup_had_periodic_run
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
                        session_started_at = write_session_start_time(now)
                        backup_last_run_at = None
                        backup_had_periodic_run = False
                        backup_periodic_runs = 0

                    # Number of interval boundaries crossed since session start.
                    due_runs = int((now - session_started_at) // BACKUP_INTERVAL_SECONDS)
                    if due_runs > backup_periodic_runs:
                        should_run_periodic_backup = True
                elif is_off and session_started_at is not None:
                    # Session ended (truly off): always run one final backup.
                    # Do not clear during transitional states like activating/deactivating.
                    should_run_shutdown_backup = True

                    clear_session_start_time()
                    backup_last_run_at = None
                    backup_had_periodic_run = False
                    backup_periodic_runs = 0

            if should_run_periodic_backup:
                # Keep schedule counters in sync only when backup succeeds.
                if run_backup_script(track_session_schedule=True):
                    with backup_lock:
                        backup_had_periodic_run = True
                        backup_periodic_runs += 1

            if should_run_shutdown_backup:
                run_backup_script(track_session_schedule=False)
        except Exception:
            # Keep watcher alive on transient command failures.
            pass

        time.sleep(15)

def start_backup_session_watcher():
    """Start backup scheduler in a daemon thread."""
    watcher = threading.Thread(target=backup_session_watcher, daemon=True)
    watcher.start()

def initialize_session_tracking():
    """Initialize session.txt on process boot and reconcile with service state."""
    ensure_session_file()
    service_status = get_status()
    session_start = read_session_start_time()

    if service_status == "active":
        # Keep existing start time when present; seed only if missing/invalid.
        if session_start is None:
            write_session_start_time()
        return

    # Service is off: session file should be empty.
    # Keep value during transitional states (activating/deactivating).
    if service_status in ("inactive", "failed") and session_start is not None:
        clear_session_start_time()

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
    backup_status, backup_status_class = get_backup_status()
    return render_template_string(
        HTML,
        service_status=service_status_display,
        service_status_class=get_service_status_class(service_status_display),
        service_running_status=service_status,
        backups_status=get_backups_status(),
        cpu_per_core_text=format_cpu_per_core_text(cpu_per_core),
        cpu_per_core_items=get_cpu_per_core_items(cpu_per_core),
        cpu_frequency=cpu_frequency,
        cpu_frequency_class=get_cpu_frequency_class(cpu_frequency),
        storage_usage=storage_usage,
        storage_usage_class=get_storage_usage_class(storage_usage),
        players_online=players_online,
        tick_rate=tick_rate,
        session_duration=session_duration,
        idle_countdown=get_idle_countdown(service_status, players_online),
        backup_status=backup_status,
        backup_status_class=backup_status_class,
        last_backup_time=backup_schedule["last_backup_time"],
        next_backup_time=backup_schedule["next_backup_time"],
        ram_usage=ram_usage,
        ram_usage_class=get_ram_usage_class(ram_usage),
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
                ["sudo", "-S", "journalctl", "-u", SERVICE, "-f", "-n", "0", "--no-pager"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if proc.stdin:
                proc.stdin.write(f"{SUDO_PASSWORD}\n")
                proc.stdin.flush()
                proc.stdin.close()

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
    backup_status, backup_status_class = get_backup_status()
    return jsonify({
        "service_status": service_status_display,
        "service_status_class": get_service_status_class(service_status_display),
        "service_running_status": service_status,
        "ram_usage": ram_usage,
        "ram_usage_class": get_ram_usage_class(ram_usage),
        "cpu_per_core_text": format_cpu_per_core_text(cpu_per_core),
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
    })

@app.route("/start", methods=["POST"])
def start():
    """Start Minecraft service and initialize backup session state."""
    global backup_last_run_at
    global backup_had_periodic_run
    global backup_periodic_runs

    # Start via systemd so status/auto-watchers remain aligned to one source.
    # Start service using systemd as source-of-truth for process lifecycle.
    subprocess.run(["sudo", "systemctl", "start", SERVICE])
    write_session_start_time()
    with backup_lock:
        backup_last_run_at = None
        backup_had_periodic_run = False
        backup_periodic_runs = 0
    return _ok_response()

@app.route("/stop", methods=["POST"])
def stop():
    """Stop Minecraft service using user-supplied sudo password."""
    global backup_last_run_at
    global backup_had_periodic_run
    global backup_periodic_runs

    sudo_password = request.form.get("sudo_password", "")
    if not validate_sudo_password(sudo_password):
        return _password_rejected_response()

    # Ordered shutdown path:
    # 1) RCON stop, 2) final backup, 3) systemd stop (inside helper).
    # Executes ordered shutdown (RCON stop -> backup -> systemd stop).
    graceful_stop_minecraft()
    clear_session_start_time()
    with backup_lock:
        backup_last_run_at = None
        backup_had_periodic_run = False
        backup_periodic_runs = 0
    return _ok_response()

@app.route("/backup", methods=["POST"])
def backup():
    """Run backup script manually from dashboard."""
    # Manual backup should not shift the periodic schedule anchor.
    run_backup_script(track_session_schedule=False)
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

    # Fire-and-forget command execution; output is visible in server logs.
    subprocess.run(
        ["mcrcon", "-p", RCON_PASSWORD, command],
        capture_output=True,
        text=True,
        timeout=8,
    )
    return _ok_response()

if __name__ == "__main__":
    # Start background automation loops before serving HTTP requests.
    ensure_session_tracking_initialized()
    start_idle_player_watcher()
    start_backup_session_watcher()
    app.run(host="0.0.0.0", port=8080)
