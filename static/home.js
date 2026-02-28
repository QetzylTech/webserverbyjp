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
    const LOG_SOURCE_KEYS = ["minecraft", "backup", "mcweb"];
    const LOG_SOURCE_STREAM_PATHS = {
        minecraft: "/log-stream/minecraft",
        backup: "/log-stream/backup",
        mcweb: "/log-stream/mcweb",
    };
    const LOG_SOURCE_TEXT_PATHS = {
        minecraft: "/log-text/minecraft",
        backup: "/log-text/backup",
        mcweb: "/log-text/mcweb",
    };
    let selectedLogSource = "minecraft";
    let minecraftSourceLines = [];
    let logSourceBuffers = {
        minecraft: [],
        backup: [],
        mcweb: [],
    };
    let logSourceHtml = {
        minecraft: "",
        backup: "",
        mcweb: "",
    };
    let logStreams = {
        minecraft: null,
        backup: null,
        mcweb: null,
    };
    let logAutoScrollEnabled = true;

    // Refresh cadence configuration (milliseconds).
    const ACTIVE_COUNTDOWN_INTERVAL_MS = 5000;

    let metricsEventSource = null;
    let countdownTimer = null;
    // Current scheduler mode: "active" or "off".
    let refreshMode = null;

    function isLogNearBottom(target, thresholdPx = 24) {
        if (!target) return true;
        const distance = target.scrollHeight - target.clientHeight - target.scrollTop;
        return distance <= thresholdPx;
    }

    function scrollLogToBottom() {
        const target = document.getElementById("minecraft-log");
        if (!target) return;
        target.scrollTop = target.scrollHeight;
    }

    function getLogSource() {
        const select = document.getElementById("log-source");
        const value = (select && select.value) ? select.value : "minecraft";
        if (value === "backup") return "backup";
        if (value === "mcweb") return "mcweb";
        return "minecraft";
    }

    function capTail(lines, maxLines) {
        if (!Array.isArray(lines)) return [];
        return lines.length > maxLines ? lines.slice(-maxLines) : lines;
    }

    function isRconNoiseLine(line) {
        const lower = (line || "").toLowerCase();
        if (lower.includes("thread rcon client")) return true;
        if (lower.includes("minecraft/rconclient") && lower.includes("shutting down")) return true;
        return false;
    }

    function shouldStoreRconNoise() {
        const hideRcon = document.getElementById("hide-rcon-noise");
        return !hideRcon || !hideRcon.checked;
    }

    function updateLogSourceUi() {
        const hideRcon = document.getElementById("hide-rcon-noise");
        if (hideRcon) hideRcon.disabled = selectedLogSource !== "minecraft";
    }

    function rebuildMinecraftVisibleBuffer() {
        let lines = minecraftSourceLines.slice();
        if (!shouldStoreRconNoise()) {
            lines = lines.filter((line) => !isRconNoiseLine(line));
        }
        logSourceBuffers.minecraft = capTail(lines, 500);
        logSourceHtml.minecraft = formatLogHtmlForSource("minecraft");
    }

    function setSourceLogText(source, rawText) {
        const lines = (rawText || "").split("\n");
        if (source === "minecraft") {
            minecraftSourceLines = capTail(lines, 2000);
            rebuildMinecraftVisibleBuffer();
            return;
        }
        logSourceBuffers[source] = capTail(lines, 200);
        logSourceHtml[source] = formatLogHtmlForSource(source);
    }

    function appendSourceLogLine(source, line) {
        const text = line || "";
        if (source === "minecraft") {
            minecraftSourceLines.push(text);
            minecraftSourceLines = capTail(minecraftSourceLines, 2000);
            if (!shouldStoreRconNoise() && isRconNoiseLine(text)) {
                return;
            }
            logSourceBuffers.minecraft.push(text);
            logSourceBuffers.minecraft = capTail(logSourceBuffers.minecraft, 500);
            logSourceHtml.minecraft = formatLogHtmlForSource("minecraft");
            return;
        }
        logSourceBuffers[source].push(text);
        logSourceBuffers[source] = capTail(logSourceBuffers[source], 200);
        logSourceHtml[source] = formatLogHtmlForSource(source);
    }

    function escapeHtml(text) {
        return (text || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
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
        const raw = line || "";
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

    function formatLogHtmlForSource(source) {
        const lines = logSourceBuffers[source] || [];
        const formatter = source === "minecraft"
            ? formatMinecraftLogLine
            : formatNonMinecraftLogLine;
        if (lines.length === 0) {
            return formatNonMinecraftLogLine("(no logs)");
        }
        return lines.map(formatter).join("");
    }

    function renderActiveLog() {
        const target = document.getElementById("minecraft-log");
        if (!target) return;
        const wasNearBottom = isLogNearBottom(target);
        target.innerHTML = logSourceHtml[selectedLogSource] || formatLogHtmlForSource(selectedLogSource);
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
            if (selectedLogSource === source) {
                renderActiveLog();
            }
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

    function showErrorModal(message) {
        // Never stack error modal on top of the password modal.
        closeSudoModal();
        const modal = document.getElementById("error-modal");
        const text = document.getElementById("error-modal-text");
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
            if (startBtn) startBtn.disabled = false;
            if (stopBtn) stopBtn.disabled = true;
            if (rconInput) rconInput.disabled = true;
            if (rconSubmit) rconSubmit.disabled = true;
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
                    showErrorModal(message);
                }
                return;
            }

            await refreshMetrics();
        } catch (err) {
            showErrorModal("Action failed. Please try again.");
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
        stopMetricsStream();
        if (homeHeartbeatTimer) {
            clearInterval(homeHeartbeatTimer);
            homeHeartbeatTimer = null;
        }
        clearRefreshTimers();
    }

    window.addEventListener("load", () => {
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
        if (errorOk) {
            errorOk.addEventListener("click", () => {
                const modal = document.getElementById("error-modal");
                if (modal) modal.classList.remove("open");
            });
        }

        if (alertMessage) {
            if (alertMessageCode === "password_incorrect") {
                showMessageModal(alertMessage);
            } else {
                showErrorModal(alertMessage);
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
        const hideRcon = document.getElementById("hide-rcon-noise");
        if (hideRcon) {
            hideRcon.addEventListener("change", () => {
                rebuildMinecraftVisibleBuffer();
                if (selectedLogSource === "minecraft") {
                    renderActiveLog();
                }
            });
        }
        const logSource = document.getElementById("log-source");
        if (logSource) {
            logSource.addEventListener("change", async () => {
                selectedLogSource = getLogSource();
                updateLogSourceUi();
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
        }
        selectedLogSource = getLogSource();
        updateLogSourceUi();
        setSourceLogText("minecraft", existingLog ? existingLog.textContent : "");
        if (existingLog) {
            renderActiveLog();
            scrollLogToBottom();
        }
        activateLogStream(selectedLogSource);
        loadLogSourceFromServer(selectedLogSource);
        startMetricsStream();
        const service = document.getElementById("service-status");
        applyRefreshMode(service ? service.textContent : "");
    });

    window.addEventListener("pagehide", teardownRealtimeConnections);
    window.addEventListener("beforeunload", teardownRealtimeConnections);
