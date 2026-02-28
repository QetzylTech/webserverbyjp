    const darkModeQuery = window.matchMedia("(prefers-color-scheme: dark)");
    function applyThemePreference() {
        document.documentElement.classList.toggle("theme-dark", darkModeQuery.matches);
    }
    applyThemePreference();
    if (darkModeQuery.addEventListener) {
        darkModeQuery.addEventListener("change", applyThemePreference);
    } else if (darkModeQuery.addListener) {
        darkModeQuery.addListener(applyThemePreference);
    }

    // `alert_message` is set server-side when an action fails validation.
    const __MCWEB_HOME_CONFIG = window.__MCWEB_HOME_CONFIG || {};
    const alertMessage = __MCWEB_HOME_CONFIG.alertMessage ?? "";
    const alertMessageCode = __MCWEB_HOME_CONFIG.alertMessageCode ?? "";
    const csrfToken = __MCWEB_HOME_CONFIG.csrfToken ?? "";
    const HOME_PAGE_HEARTBEAT_INTERVAL_MS = Number(__MCWEB_HOME_CONFIG.heartbeatIntervalMs || 10000);
    function sendHomePageHeartbeat() {
        fetch("/home-heartbeat", {
            method: "POST",
            headers: {
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRF-Token": csrfToken || "",
            },
            cache: "no-store",
            keepalive: true,
        }).catch(() => {});
    }
    sendHomePageHeartbeat();
    let homeHeartbeatTimer = window.setInterval(sendHomePageHeartbeat, HOME_PAGE_HEARTBEAT_INTERVAL_MS);

    // UI state used for dynamic controls/modals.
    let idleCountdownSeconds = null;
    let pendingSudoForm = null;
    const LOG_SOURCE_KEYS = ["minecraft", "backup", "mcweb", "mcweb_log"];
    const LOG_SOURCE_STREAM_PATHS = {
        minecraft: "/log-stream/minecraft",
        backup: "/log-stream/backup",
        mcweb: "/log-stream/mcweb",
        mcweb_log: "/log-stream/mcweb_log",
    };
    const LOG_SOURCE_TEXT_PATHS = {
        minecraft: "/log-text/minecraft",
        backup: "/log-text/backup",
        mcweb: "/log-text/mcweb",
        mcweb_log: "/log-text/mcweb_log",
    };
    let selectedLogSource = "minecraft";
    let logSourceBuffers = {
        minecraft: [],
        backup: [],
        mcweb: [],
        mcweb_log: [],
    };
    let pendingLogLines = {
        minecraft: [],
        backup: [],
        mcweb: [],
        mcweb_log: [],
    };
    let pendingLogFlushTimers = {
        minecraft: null,
        backup: null,
        mcweb: null,
        mcweb_log: null,
    };
    const LOG_STREAM_BATCH_FLUSH_MS = 75;
    let logStreams = {
        minecraft: null,
        backup: null,
        mcweb: null,
        mcweb_log: null,
    };
    let deviceNameMap = {};
    let logAutoScrollEnabled = true;

    // Refresh cadence configuration (milliseconds).
    const ACTIVE_COUNTDOWN_INTERVAL_MS = 5000;

    let metricsEventSource = null;
    let metricsPollTimer = null;
    let countdownTimer = null;
    let lowStorageModalShown = false;
    let lastBackupWarningSeq = 0;
    // Current scheduler mode: "active" or "off".
    let refreshMode = null;

    function isLogNearBottom(target, thresholdPx = 24) {
        if (!target) return true;
        const distance = target.scrollHeight - target.clientHeight - target.scrollTop;
        return distance <= thresholdPx;
    }
    function syncVerticalScrollbarClass(target) {
        if (!target) return;
        const hasVerticalScrollbar = target.scrollHeight > target.clientHeight + 1;
        target.classList.toggle("has-vscroll", hasVerticalScrollbar);
    }
    function watchVerticalScrollbarClass(target) {
        if (!target) return;
        syncVerticalScrollbarClass(target);
        target.addEventListener("scroll", () => syncVerticalScrollbarClass(target), { passive: true });
        window.addEventListener("resize", () => syncVerticalScrollbarClass(target));
        if (window.ResizeObserver) {
            const ro = new ResizeObserver(() => syncVerticalScrollbarClass(target));
            ro.observe(target);
        }
    }

    function scrollLogToBottom() {
        const target = document.getElementById("minecraft-log");
        if (!target) return;
        target.scrollTop = target.scrollHeight;
    }

    function getLogSource() {
        const select = document.getElementById("log-source");
        const value = (select && select.value) ? select.value : "minecraft";
        if (LOG_SOURCE_KEYS.includes(value)) return value;
        return "minecraft";
    }

    function capTail(lines, maxLines) {
        if (!Array.isArray(lines)) return [];
        return lines.length > maxLines ? lines.slice(-maxLines) : lines;
    }

    function sourceBufferLimit(source) {
        return source === "minecraft" ? 500 : 200;
    }

    function setSourceLogText(source, rawText) {
        const lines = capTail((rawText || "").split("\n"), sourceBufferLimit(source));
        logSourceBuffers[source] = lines.map((line) => buildLogEntry(source, line));
    }

    function appendSourceLogLine(source, line) {
        if (!LOG_SOURCE_KEYS.includes(source)) return;
        pendingLogLines[source].push(line || "");
        if (pendingLogFlushTimers[source]) return;
        pendingLogFlushTimers[source] = window.setTimeout(() => {
            pendingLogFlushTimers[source] = null;
            flushPendingLogLines(source);
        }, LOG_STREAM_BATCH_FLUSH_MS);
    }

    function flushPendingLogLines(source) {
        const pending = pendingLogLines[source];
        if (!pending || pending.length === 0) return;
        pendingLogLines[source] = [];

        const nextEntries = pending.map((line) => buildLogEntry(source, line));
        const targetBuffer = logSourceBuffers[source];
        const previousLength = targetBuffer.length;
        targetBuffer.push(...nextEntries);

        const limit = sourceBufferLimit(source);
        const overflow = Math.max(0, targetBuffer.length - limit);
        if (overflow > 0) {
            targetBuffer.splice(0, overflow);
        }
        if (selectedLogSource !== source) return;

        appendRenderedEntriesToActiveLog(nextEntries, {
            previousLength,
            droppedCount: overflow,
            currentLength: targetBuffer.length,
        });
    }

    function appendRenderedEntriesToActiveLog(entries, meta) {
        const target = document.getElementById("minecraft-log");
        if (!target) return;
        const wasNearBottom = isLogNearBottom(target);
        if ((meta.previousLength || 0) === 0) {
            target.innerHTML = "";
        }
        const htmlChunk = entries.map((entry) => entry.html).join("");
        if (htmlChunk) {
            target.insertAdjacentHTML("beforeend", htmlChunk);
        }
        const droppedCount = Number(meta.droppedCount || 0);
        for (let i = 0; i < droppedCount; i += 1) {
            if (!target.firstElementChild) break;
            target.removeChild(target.firstElementChild);
        }
        if ((meta.currentLength || 0) === 0) {
            target.innerHTML = formatNonMinecraftLogLine("(no logs)");
        }
        if (logAutoScrollEnabled && wasNearBottom) {
            scrollLogToBottom();
        }
    }

    function escapeHtml(text) {
        return (text || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function ipReplacement(ipText) {
        const ip = (ipText || "").trim();
        if (!ip) return "unmapped-device";
        const mapped = deviceNameMap[ip];
        return mapped && mapped.trim() ? mapped.trim() : "unmapped-device";
    }

    function replaceIpsWithDeviceNames(text) {
        const raw = text || "";
        const withIpv4 = raw.replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, (ip) => ipReplacement(ip));
        return withIpv4.replace(/\b(?:[A-Fa-f0-9]{0,4}:){3,7}[A-Fa-f0-9]{0,4}\b/g, (ip) => ipReplacement(ip));
    }

    function bracketClass(token) {
        if (/^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\]$/.test(token)) return "log-ts";
        if (/[/]\s*error\]/i.test(token) || /[/]\s*fatal\]/i.test(token)) return "log-level-error";
        if (/[/]\s*warn\]/i.test(token)) return "log-level-warn";
        if (/[/]\s*info\]/i.test(token)) return "log-level-info";
        return "log-bracket";
    }

    function formatTextSegment(text, isLineStart) {
        if (!text) return "";
        if (isLineStart) {
            const m = text.match(/^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})(\s+.*)?$/);
            if (m) {
                const ts = `<span class="log-ts">${escapeHtml(m[1])}</span>`;
                const rest = m[2] ? `<span class="log-text">${escapeHtml(m[2])}</span>` : "";
                return ts + rest;
            }
        }
        return `<span class="log-text">${escapeHtml(text)}</span>`;
    }

    function formatBracketAwareLogLine(line, highlightErrorLine) {
        const raw = replaceIpsWithDeviceNames(line || "");
        if (highlightErrorLine) {
            const lower = raw.toLowerCase();
            if (lower.includes("error") || lower.includes("overloaded") || lower.includes("delayed")) {
                return `<span class="log-line log-level-error">${escapeHtml(raw)}</span>`;
            }
        }
        const bracketRe = /\[[^\]]*\]/g;
        let out = "";
        let cursor = 0;
        let firstSegment = true;
        let match;
        while ((match = bracketRe.exec(raw)) !== null) {
            const start = match.index;
            const end = start + match[0].length;
            out += formatTextSegment(raw.slice(cursor, start), firstSegment);
            out += `<span class="${bracketClass(match[0])}">${escapeHtml(match[0])}</span>`;
            cursor = end;
            firstSegment = false;
        }
        out += formatTextSegment(raw.slice(cursor), firstSegment);
        return `<span class="log-line">${out || '<span class="log-muted">(empty line)</span>'}</span>`;
    }

    function formatMinecraftLogLine(line) {
        return formatBracketAwareLogLine(line, true);
    }

    function formatNonMinecraftLogLine(line) {
        // For backup/mcweb logs: color only timestamps and bracketed tokens.
        return formatBracketAwareLogLine(line, false);
    }

    function buildLogEntry(source, line) {
        const raw = line || "";
        const formatter = source === "minecraft" ? formatMinecraftLogLine : formatNonMinecraftLogLine;
        return { raw, html: formatter(raw) };
    }

    function renderActiveLog() {
        const target = document.getElementById("minecraft-log");
        if (!target) return;
        const wasNearBottom = isLogNearBottom(target);
        const entries = logSourceBuffers[selectedLogSource] || [];
        if (entries.length === 0) {
            target.innerHTML = formatNonMinecraftLogLine("(no logs)");
        } else {
            target.innerHTML = entries.map((entry) => entry.html).join("");
        }
        if (logAutoScrollEnabled && wasNearBottom) {
            scrollLogToBottom();
        }
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

    function ensureLogStreamStarted(source) {
        if (logStreams[source]) return;
        const path = LOG_SOURCE_STREAM_PATHS[source];
        if (!path) return;
        const stream = new EventSource(path);
        stream.onmessage = (event) => {
            appendSourceLogLine(source, event.data || "");
        };
        stream.onerror = () => {
            // EventSource reconnects automatically.
        };
        logStreams[source] = stream;
    }

    function closeLogStream(source) {
        const stream = logStreams[source];
        if (!stream) return;
        try {
            stream.close();
        } catch (_) {
            // Ignore close errors during navigation teardown.
        }
        logStreams[source] = null;
    }

    function activateLogStream(source) {
        LOG_SOURCE_KEYS.forEach((key) => {
            if (key !== source) {
                closeLogStream(key);
            }
        });
        ensureLogStreamStarted(source);
    }

    async function loadLogSourceFromServer(source) {
        try {
            const response = await fetch(LOG_SOURCE_TEXT_PATHS[source], { cache: "no-store" });
            if (!response.ok) {
                setSourceLogText(source, "(no logs)");
                return;
            }
            const payload = await response.json();
            setSourceLogText(source, payload.logs || "");
        } catch (err) {
            setSourceLogText(source, "(no logs)");
        }
        if (selectedLogSource === source) {
            renderActiveLog();
        }
    }

    async function loadDeviceNameMap() {
        try {
            const response = await fetch("/device-name-map", { cache: "no-store" });
            if (!response.ok) return;
            const payload = await response.json();
            const nextMap = payload && payload.map ? payload.map : {};
            deviceNameMap = (nextMap && typeof nextMap === "object") ? nextMap : {};
            LOG_SOURCE_KEYS.forEach((source) => {
                if ((logSourceBuffers[source] || []).length > 0) {
                    logSourceBuffers[source] = logSourceBuffers[source].map((entry) => buildLogEntry(source, entry.raw));
                }
            });
            renderActiveLog();
        } catch (_) {
            // Keep IP redaction fallback labels even when map fetch fails.
        }
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

    function summarizeError(errorCode, action) {
        if (errorCode === "csrf_invalid") {
            return "Security check failed. Refresh the page and try again.";
        }
        if (errorCode === "backup_failed") {
            return "Backup did not complete. Check backup status/logs and retry.";
        }
        if (errorCode === "backup_warning") {
            return "Backup completed with warnings. Review backup logs.";
        }
        if (errorCode === "internal_error") {
            return "The server hit an unexpected problem while processing your request.";
        }
        if (errorCode === "start_failed") {
            return "Server failed to start. Check service logs and configuration.";
        }
        if (errorCode === "low_storage_space") {
            return "Storage space is too low. Free at least 10% before starting the server.";
        }
        if (action === "/rcon") {
            return "Command could not be completed.";
        }
        if (action === "/backup") {
            return "Backup could not be completed.";
        }
        return "The action could not be completed.";
    }

    function showErrorModal(message, options = {}) {
        // Never stack error modal on top of the password modal.
        closeSudoModal();
        const modal = document.getElementById("error-modal");
        const text = document.getElementById("error-modal-text");
        const moreBtn = document.getElementById("error-modal-more");
        const details = document.getElementById("error-modal-details");
        if (!modal || !text) return;
        const code = (options.errorCode || "").trim();
        const action = (options.action || "").trim();
        const summary = summarizeError(code, action);
        text.textContent = summary;
        if (moreBtn && details) {
            const detailParts = [];
            if (code) detailParts.push(`Error code: ${code}`);
            if (action) detailParts.push(`Action: ${action}`);
            if (message) detailParts.push(`Message: ${message}`);
            const detailText = detailParts.join("\n");
            const hasDetails = detailText.length > 0;
            details.textContent = detailText;
            details.hidden = true;
            moreBtn.textContent = "Show more";
            moreBtn.hidden = !hasDetails;
        }
        modal.setAttribute("aria-hidden", "false");
        modal.classList.add("open");
        fetch("/ui-error-log", {
            method: "POST",
            headers: {
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRF-Token": csrfToken || "",
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                error_code: code || "",
                action: action || "",
                message: message || "",
            }),
            keepalive: true,
            cache: "no-store",
        }).catch(() => {});
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

    function applyMetricsData(data) {
        if (!data) return;
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
        const controlPanelTitle = document.getElementById("control-panel-title");
        const startBtn = document.getElementById("start-btn");
        const stopBtn = document.getElementById("stop-btn");
        const backupBtn = document.getElementById("backup-btn");
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
        if (backupBtn && data.backup_status) backupBtn.disabled = data.backup_status === "Running";
        if (lastBackup && data.last_backup_time) lastBackup.textContent = data.last_backup_time;
        if (nextBackup && data.next_backup_time) nextBackup.textContent = data.next_backup_time;
        if (backupsStatus && data.backups_status) backupsStatus.textContent = data.backups_status;
        if (service && data.service_status) service.textContent = data.service_status;
        if (service && data.service_status_class) service.className = data.service_status_class;
        if (serverTime && data.server_time) serverTime.textContent = data.server_time;
        if (controlPanelTitle && data.world_name) controlPanelTitle.textContent = `${data.world_name} Control Panel`;
        if (serviceDurationPrefix && service && sessionDuration) {
            if (data.service_status === "Running" && data.session_duration && data.session_duration !== "--") {
                sessionDuration.style.display = "";
                serviceDurationPrefix.textContent = " for ";
            } else {
                sessionDuration.style.display = "none";
                serviceDurationPrefix.textContent = "";
            }
        }
        const rconEnabled = data.rcon_enabled === true;
        const lowStorageBlocked = data.low_storage_blocked === true;
        const lowStorageMessage = (data.low_storage_message || "").trim();
        const backupWarningSeq = Number(data.backup_warning_seq || 0);
        const backupWarningMessage = (data.backup_warning_message || "").trim();
        if (data.service_running_status === "active") {
            if (startBtn) startBtn.disabled = true;
            if (stopBtn) stopBtn.disabled = false;
            if (rconInput) rconInput.disabled = !rconEnabled;
            if (rconSubmit) rconSubmit.disabled = !rconEnabled;
            if (rconInput) {
                rconInput.placeholder = rconEnabled
                    ? "Enter Minecraft server command"
                    : "RCON unavailable (missing rcon.password)";
            }
        } else {
            if (startBtn) startBtn.disabled = lowStorageBlocked;
            if (stopBtn) stopBtn.disabled = true;
            if (rconInput) rconInput.disabled = true;
            if (rconSubmit) rconSubmit.disabled = true;
        }
        if (lowStorageBlocked) {
            if (!lowStorageModalShown && lowStorageMessage) {
                showErrorModal(lowStorageMessage, {
                    errorCode: "low_storage_space",
                    action: window.location.pathname || "/",
                });
                lowStorageModalShown = true;
            }
        } else {
            lowStorageModalShown = false;
        }
        if (backupWarningMessage && backupWarningSeq > lastBackupWarningSeq) {
            showErrorModal(backupWarningMessage, {
                errorCode: "backup_warning",
                action: "/backup",
            });
            lastBackupWarningSeq = backupWarningSeq;
        } else if (backupWarningSeq > lastBackupWarningSeq) {
            lastBackupWarningSeq = backupWarningSeq;
        }
        applyRefreshMode(data.service_status);
    }

    async function submitFormAjax(form, sudoPassword = undefined) {
        if (!form) return;
        const action = form.getAttribute("action") || "/";
        const method = (form.getAttribute("method") || "POST").toUpperCase();
        if (action === "/backup") {
            const backupStatus = document.getElementById("backup-status");
            if (backupStatus) {
                backupStatus.textContent = "Running";
                backupStatus.className = "stat-green";
            }
        }
        const formData = new FormData(form);
        if (sudoPassword !== undefined) {
            formData.set("sudo_password", sudoPassword);
        }
        try {
            const response = await fetch(action, {
                method,
                body: formData,
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json",
                    "X-CSRF-Token": csrfToken
                }
            });

            let payload = {};
            try {
                payload = await response.json();
            } catch (e) {
                payload = {};
            }

            if (!response.ok || payload.ok === false) {
                const message = (payload && payload.message) ? payload.message : "Action rejected.";
                const isPasswordRejected =
                    payload &&
                    payload.error === "password_incorrect" &&
                    (action === "/stop" || action === "/rcon");
                if (isPasswordRejected) {
                    showMessageModal(message);
                } else {
                    showErrorModal(message, {
                        errorCode: payload && payload.error ? String(payload.error) : "",
                        action,
                    });
                }
                return;
            }

            await refreshMetrics();
        } catch (err) {
            showErrorModal("Network request failed.", {
                errorCode: "network_error",
                action,
            });
        }
    }

    async function refreshMetrics() {
        try {
            const response = await fetch("/metrics", { cache: "no-store" });
            if (!response.ok) return;
            const data = await response.json();
            applyMetricsData(data);
        } catch (err) {
            // Keep current metrics on network/read errors.
        }
    }

    function startMetricsStream() {
        if (metricsEventSource) return;
        metricsEventSource = new EventSource("/metrics-stream");
        metricsEventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data || "{}");
                applyMetricsData(data);
            } catch (err) {
                // Ignore malformed stream payload.
            }
        };
        metricsEventSource.onerror = () => {
            // EventSource reconnects automatically.
        };
    }

    function stopMetricsStream() {
        if (!metricsEventSource) return;
        try {
            metricsEventSource.close();
        } catch (_) {
            // Ignore close errors during teardown.
        }
        metricsEventSource = null;
    }

    function startMetricsPolling() {
        if (metricsPollTimer) return;
        refreshMetrics();
        metricsPollTimer = window.setInterval(refreshMetrics, 5000);
    }

    function stopMetricsPolling() {
        if (!metricsPollTimer) return;
        clearInterval(metricsPollTimer);
        metricsPollTimer = null;
    }

    function clearRefreshTimers() {
        // Prevent duplicate interval loops when switching modes.
        if (countdownTimer) {
            clearInterval(countdownTimer);
            countdownTimer = null;
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
            return;
        }

        // In Active mode, restore countdown.
        countdownTimer = setInterval(tickIdleCountdown, ACTIVE_COUNTDOWN_INTERVAL_MS);
    }

    function initSidebarNav() {
        const toggle = document.getElementById("nav-toggle");
        const sidebar = document.getElementById("side-nav");
        const backdrop = document.getElementById("nav-backdrop");
        if (!toggle || !sidebar || !backdrop) return;

        function closeNav() {
            sidebar.classList.remove("open");
            backdrop.classList.remove("open");
            toggle.classList.remove("nav-open");
            toggle.setAttribute("aria-expanded", "false");
        }

        function toggleNav() {
            const nextOpen = !sidebar.classList.contains("open");
            sidebar.classList.toggle("open", nextOpen);
            backdrop.classList.toggle("open", nextOpen);
            toggle.classList.toggle("nav-open", nextOpen);
            toggle.setAttribute("aria-expanded", nextOpen ? "true" : "false");
        }

        toggle.addEventListener("click", toggleNav);
        backdrop.addEventListener("click", closeNav);
        window.addEventListener("resize", () => {
            if (window.innerWidth > 1100) closeNav();
        });
    }

    function teardownRealtimeConnections() {
        LOG_SOURCE_KEYS.forEach((source) => closeLogStream(source));
        LOG_SOURCE_KEYS.forEach((source) => {
            const timerId = pendingLogFlushTimers[source];
            if (timerId) {
                clearTimeout(timerId);
                pendingLogFlushTimers[source] = null;
            }
            pendingLogLines[source] = [];
        });
        stopMetricsStream();
        stopMetricsPolling();
        if (homeHeartbeatTimer) {
            clearInterval(homeHeartbeatTimer);
            homeHeartbeatTimer = null;
        }
        clearRefreshTimers();
    }

    window.addEventListener("load", async () => {
        initSidebarNav();
        document.querySelectorAll("form.ajax-form:not(.sudo-form)").forEach((form) => {
            form.addEventListener("submit", async (event) => {
                event.preventDefault();
                await submitFormAjax(form);
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
                await submitFormAjax(form, password);
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
        const errorOk = document.getElementById("error-modal-ok");
        const errorMore = document.getElementById("error-modal-more");
        if (errorOk) {
            errorOk.addEventListener("click", () => {
                const modal = document.getElementById("error-modal");
                if (modal) modal.classList.remove("open");
            });
        }
        if (errorMore) {
            errorMore.addEventListener("click", () => {
                const details = document.getElementById("error-modal-details");
                if (!details) return;
                const nextHidden = !details.hidden;
                details.hidden = nextHidden;
                errorMore.textContent = nextHidden ? "Show more" : "Show less";
            });
        }

        if (alertMessage) {
            if (alertMessageCode === "password_incorrect") {
                showMessageModal(alertMessage);
            } else {
                showErrorModal(alertMessage, {
                    errorCode: alertMessageCode,
                    action: window.location.pathname || "",
                });
                if (alertMessageCode === "low_storage_space") {
                    lowStorageModalShown = true;
                }
            }
            const url = new URL(window.location.href);
            url.searchParams.delete("msg");
            window.history.replaceState({}, "", url.pathname + (url.search ? url.search : "") + url.hash);
        }
        scrollLogToBottom();
        const idleCountdown = document.getElementById("idle-countdown");
        if (idleCountdown) {
            idleCountdownSeconds = parseCountdown(idleCountdown.textContent.trim());
        }
        const logSource = document.getElementById("log-source");
        if (logSource) {
            logSource.addEventListener("change", async () => {
                selectedLogSource = getLogSource();
                activateLogStream(selectedLogSource);
                if ((logSourceBuffers[selectedLogSource] || []).length === 0) {
                    await loadLogSourceFromServer(selectedLogSource);
                }
                renderActiveLog();
                scrollLogToBottom();
            });
        }
        const existingLog = document.getElementById("minecraft-log");
        if (existingLog) {
            existingLog.addEventListener("scroll", () => {
                logAutoScrollEnabled = isLogNearBottom(existingLog);
            });
            watchVerticalScrollbarClass(existingLog);
        }
        selectedLogSource = getLogSource();
        await loadDeviceNameMap();
        setSourceLogText("minecraft", existingLog ? existingLog.textContent : "");
        if (existingLog) {
            renderActiveLog();
            scrollLogToBottom();
        }
        activateLogStream(selectedLogSource);
        loadLogSourceFromServer(selectedLogSource);
        startMetricsStream();
        startMetricsPolling();
        const service = document.getElementById("service-status");
        applyRefreshMode(service ? service.textContent : "");
    });

    window.addEventListener("pagehide", teardownRealtimeConnections);
    window.addEventListener("beforeunload", teardownRealtimeConnections);
