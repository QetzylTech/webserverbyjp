(function () {
    // `alert_message` is set server-side when an action fails validation.
    const __MCWEB_HOME_CONFIG = window.__MCWEB_HOME_CONFIG || {};
    const alertMessage = __MCWEB_HOME_CONFIG.alertMessage ?? "";
    const alertMessageCode = __MCWEB_HOME_CONFIG.alertMessageCode ?? "";
    const csrfToken = __MCWEB_HOME_CONFIG.csrfToken ?? "";
    const shellConfig = window.__MCWEB_SHELL_CONFIG || {};
    const passwordRequired = shellConfig.passwordRequired !== false;
    const http = window.MCWebHttp || null;
    const shell = window.MCWebShell || null;
    const domUtils = window.MCWebDomUtils || {};
    const createCleanupStack = typeof domUtils.createCleanupStack === "function"
        ? () => domUtils.createCleanupStack()
        : () => null;
    let homeCleanup = null;
    const logUtils = window.MCWebLogUtils || {};
    const homeLogRuntime = window.MCWebHomeLogRuntime || {};
    const homeTimeUtils = window.MCWebHomeTimeUtils || {};
    const pageModules = window.MCWebPageModules || null;
    const HOME_PAGE_HEARTBEAT_INTERVAL_MS = Number(__MCWEB_HOME_CONFIG.heartbeatIntervalMs || 10000);
    const pageActivityRuntime = window.MCWebPageActivityRuntime;
    const FILE_LISTS_INVALIDATED_EVENT = "mcweb:file-lists-invalidated";
    const START_BUTTON_COOLDOWN_MS = 10000;
    const homeHeartbeatController = pageActivityRuntime.createHeartbeatController({
        path: "/home-heartbeat",
        csrfToken,
        intervalMs: HOME_PAGE_HEARTBEAT_INTERVAL_MS,
    });

    // UI state used for dynamic controls/modals.
    let idleCountdownSeconds = null;
    let idleCountdownDeadlineMs = null;
    let sessionDurationSeconds = null;
    let sessionDurationBaseSeconds = null;
    let sessionDurationBaseAtMs = null;
    let sessionDurationRunning = false;
    let serverTimeBaseUtcMs = null;
    let serverTimeBaseAtMs = null;
    let serverTimeZoneLabel = "";
    let pendingSudoForm = null;
    const LOG_SOURCE_KEYS = ["minecraft", "backup", "mcweb", "mcweb_log"];
    let selectedLogSource = "minecraft";
    let logAutoScrollEnabled = true;

    // Refresh cadence configuration (milliseconds).
    const TIMER_REBASE_TOLERANCE_SECONDS = 2;

    let teardownHomePage = null;
    let logScrollbarCleanup = null;
    let countdownTimer = null;
    let serverClockTimer = null;
    let startCooldownTimer = null;
    const operationPollTimers = {};
    let lowStorageModalShown = false;
    let lastBackupWarningSeq = 0;
    let cachedMetricsSnapshot = null;
    let backupStatusOverride = "";
    let queuedStartPending = false;
    let startCooldownUntilMs = 0;
    // Current scheduler mode: "active" or "off".
    let refreshMode = null;
    const SERVER_TIME_REBASE_TOLERANCE_SECONDS = 2;

    const watchVerticalScrollbarClass = typeof domUtils.watchVerticalScrollbarClass === "function"
        ? (target) => domUtils.watchVerticalScrollbarClass(target)
        : () => {};
    const homeLogController = homeLogRuntime && typeof homeLogRuntime.createHomeLogController === "function"
        ? homeLogRuntime.createHomeLogController({ shell, logUtils, watchVerticalScrollbarClass })
        : null;
    if (homeLogController) {
        selectedLogSource = homeLogController.getSelectedSource();
        logAutoScrollEnabled = homeLogController.getAutoScrollEnabled();
    }

    function scrollLogToBottom() {
        if (homeLogController) {
            homeLogController.scrollLogToBottom();
        }
    }

    function getLogSource() {
        const select = document.getElementById("log-source");
        const value = (select && select.value) ? select.value : "minecraft";
        if (LOG_SOURCE_KEYS.includes(value)) return value;
        return "minecraft";
    }

    function persistHomeViewState(patch = {}) {
        if (!shell || typeof shell.updateHomeViewState !== "function") return;
        shell.updateHomeViewState(patch);
    }

    function setSourceLogText(source, rawText) {
        if (!homeLogController) return;
        homeLogController.setSourceLogText(source, rawText);
    }

    function syncHomeLogSource(source, lines) {
        if (!homeLogController) return;
        homeLogController.syncShellLogSource(source, lines);
        selectedLogSource = homeLogController.getSelectedSource();
        logAutoScrollEnabled = homeLogController.getAutoScrollEnabled();
    }

    function renderActiveLog() {
        if (!homeLogController) return;
        homeLogController.renderActiveLog();
        selectedLogSource = homeLogController.getSelectedSource();
        logAutoScrollEnabled = homeLogController.getAutoScrollEnabled();
    }

    function activateLogStream(source) {
        if (!homeLogController) return;
        homeLogController.activateLogStream(source);
    }

    async function loadLogSourceFromServer(source) {
        if (!homeLogController) return;
        await homeLogController.loadLogSourceFromServer(source);
        selectedLogSource = homeLogController.getSelectedSource();
        logAutoScrollEnabled = homeLogController.getAutoScrollEnabled();
    }

    async function loadDeviceNameMap() {
        if (!homeLogController) return;
        await homeLogController.loadDeviceNameMap();
    }

    function parseCountdown(text) {
        if (!text || text === "--:--") return null;
        return typeof homeTimeUtils.parseCountdown === "function"
            ? homeTimeUtils.parseCountdown(text)
            : null;
    }

    function parseSessionDuration(text) {
        if (!text || text === "--") return null;
        return typeof homeTimeUtils.parseSessionDuration === "function"
            ? homeTimeUtils.parseSessionDuration(text)
            : null;
    }

    function formatCountdown(totalSeconds) {
        if (totalSeconds === null) return "--:--";
        return typeof homeTimeUtils.formatCountdown === "function"
            ? homeTimeUtils.formatCountdown(totalSeconds)
            : "--:--";
    }

    function formatSessionDuration(totalSeconds) {
        if (totalSeconds === null) return "--";
        return typeof homeTimeUtils.formatSessionDuration === "function"
            ? homeTimeUtils.formatSessionDuration(totalSeconds)
            : "--";
    }

    function parseServerTimeText(text) {
        return typeof homeTimeUtils.parseServerTimeText === "function"
            ? homeTimeUtils.parseServerTimeText(text)
            : null;
    }

    function setServerTimeFromEpoch(epochMs, zoneLabel, options = {}) {
        const force = options.force === true;
        const nextMs = Number(epochMs);
        if (!Number.isFinite(nextMs) || nextMs <= 0) return false;
        const currentMs = currentServerTimeUtcMs();
        if (!force && currentMs !== null) {
            const driftSeconds = Math.abs(nextMs - currentMs) / 1000;
            if (driftSeconds <= SERVER_TIME_REBASE_TOLERANCE_SECONDS) {
                return true;
            }
        }
        serverTimeBaseUtcMs = nextMs;
        serverTimeBaseAtMs = Date.now();
        serverTimeZoneLabel = String(zoneLabel || "").trim() || serverTimeZoneLabel;
        return true;
    }

    function currentIdleCountdownSeconds() {
        if (idleCountdownDeadlineMs === null) return null;
        return Math.max(0, Math.ceil((idleCountdownDeadlineMs - Date.now()) / 1000));
    }

    function setIdleCountdownFromParsedSeconds(seconds, options = {}) {
        const force = options.force === true;
        if (seconds === null || Number.isNaN(seconds)) {
            idleCountdownSeconds = null;
            idleCountdownDeadlineMs = null;
            return;
        }
        const next = Math.max(0, Math.floor(Number(seconds)));
        const current = currentIdleCountdownSeconds();
        if (!force && current !== null && Math.abs(next - current) <= TIMER_REBASE_TOLERANCE_SECONDS) {
            idleCountdownSeconds = current;
            return;
        }
        idleCountdownSeconds = next;
        idleCountdownDeadlineMs = Date.now() + (next * 1000);
    }

    function currentSessionDurationSeconds() {
        if (sessionDurationBaseSeconds === null || sessionDurationBaseAtMs === null) return null;
        const elapsed = Math.max(0, Math.floor((Date.now() - sessionDurationBaseAtMs) / 1000));
        return sessionDurationBaseSeconds + elapsed;
    }

    function setSessionDurationFromParsedSeconds(seconds, options = {}) {
        const force = options.force === true;
        if (seconds === null || Number.isNaN(seconds)) {
            sessionDurationSeconds = null;
            sessionDurationBaseSeconds = null;
            sessionDurationBaseAtMs = null;
            return;
        }
        const normalized = Math.max(0, Math.floor(Number(seconds)));
        const current = currentSessionDurationSeconds();
        if (!force && current !== null && normalized <= (current + TIMER_REBASE_TOLERANCE_SECONDS)) {
            // Keep a monotonic local timer unless backend is meaningfully ahead.
            sessionDurationSeconds = current;
            return;
        }
        sessionDurationSeconds = normalized;
        sessionDurationBaseSeconds = normalized;
        sessionDurationBaseAtMs = Date.now();
    }

    function currentServerTimeUtcMs() {
        if (serverTimeBaseUtcMs === null || serverTimeBaseAtMs === null) return null;
        const elapsedMs = Math.max(0, Date.now() - serverTimeBaseAtMs);
        return serverTimeBaseUtcMs + elapsedMs;
    }

    function setServerTimeFromText(text, options = {}) {
        const force = options.force === true;
        const parsed = homeTimeUtils.parseServerTimeText(text);
        if (!parsed) return false;
        const nextMs = parsed.utcMs;
        const currentMs = currentServerTimeUtcMs();
        if (!force && currentMs !== null) {
            const driftSeconds = Math.abs(nextMs - currentMs) / 1000;
            if (driftSeconds <= SERVER_TIME_REBASE_TOLERANCE_SECONDS) {
                return true;
            }
        }
        serverTimeBaseUtcMs = nextMs;
        serverTimeBaseAtMs = Date.now();
        serverTimeZoneLabel = parsed.zoneLabel;
        return true;
    }

    function tickServerClock() {
        const serverTimeNode = document.getElementById("server-time");
        if (!serverTimeNode) return;
        const nowUtcMs = currentServerTimeUtcMs();
        if (nowUtcMs === null) return;
        const nextText = homeTimeUtils.formatServerTimeText(nowUtcMs, serverTimeZoneLabel);
        if (nextText) serverTimeNode.textContent = nextText;
    }

    function tickRuntimeSimulation() {
        const idleCountdown = document.getElementById("idle-countdown");
        if (idleCountdown) {
            if (idleCountdownDeadlineMs === null) {
                idleCountdown.textContent = "--:--";
            } else {
                const remaining = currentIdleCountdownSeconds();
                idleCountdownSeconds = remaining;
                idleCountdown.textContent = homeTimeUtils.formatCountdown(remaining);
            }
        }

        const sessionDuration = document.getElementById("session-duration");
        if (
            sessionDurationRunning &&
            sessionDuration &&
            sessionDurationBaseSeconds !== null &&
            sessionDurationBaseAtMs !== null
        ) {
            const current = currentSessionDurationSeconds();
            sessionDurationSeconds = current;
            sessionDuration.textContent = homeTimeUtils.formatSessionDuration(current);
        }
    }

    function scheduleRuntimeSimulationTick() {
        if (refreshMode !== "active") return;
        tickRuntimeSimulation();
        const now = Date.now();
        const driftToNextSecond = 1000 - (now % 1000);
        const delay = Math.max(250, Math.min(1250, driftToNextSecond));
        countdownTimer = window.setTimeout(scheduleRuntimeSimulationTick, delay);
    }

    function clearServerClockTimer() {
        if (!serverClockTimer) return;
        clearTimeout(serverClockTimer);
        serverClockTimer = null;
    }

    function scheduleServerClockTick() {
        clearServerClockTimer();
        tickServerClock();
        const now = Date.now();
        const driftToNextSecond = 1000 - (now % 1000);
        const delay = Math.max(250, Math.min(1250, driftToNextSecond));
        serverClockTimer = window.setTimeout(scheduleServerClockTick, delay);
    }

    function isServiceRunningInMetrics(data) {
        const explicitStatus = String(data && data.service_status ? data.service_status : "").trim().toLowerCase();
        if (explicitStatus) return explicitStatus === "running";
        const fallback = String(data && data.service_running_status ? data.service_running_status : "").trim().toLowerCase();
        return fallback === "active";
    }

    function announceBackupsListInvalidation() {
        if (shell && typeof shell.invalidateFilePageListCache === "function") {
            shell.invalidateFilePageListCache("backups");
        }
        window.dispatchEvent(new CustomEvent(FILE_LISTS_INVALIDATED_EVENT, { detail: { backups: true } }));
    }

    function isStartCooldownActive() {
        return startCooldownUntilMs > Date.now();
    }

    function setBackupStatusOverride(nextStatus) {
        backupStatusOverride = String(nextStatus || "").trim();
        if (cachedMetricsSnapshot) {
            applyMetricsData(cachedMetricsSnapshot, { fromCache: true });
            return;
        }
        const backupStatus = document.getElementById("backup-status");
        const backupBtn = document.getElementById("backup-btn");
        if (!backupStatus) return;
        if (!backupStatusOverride) return;
        backupStatus.textContent = backupStatusOverride;
        backupStatus.className = backupStatusOverride === "Running" ? "stat-green" : "stat-yellow";
        if (backupBtn) {
            backupBtn.disabled = true;
        }
    }

    function clearStartCooldownTimer() {
        if (!startCooldownTimer) return;
        clearTimeout(startCooldownTimer);
        startCooldownTimer = null;
    }

    function syncStartButtonCooldown() {
        const startBtn = document.getElementById("start-btn");
        if (!startBtn) return;
        if (!isStartCooldownActive()) {
            clearStartCooldownTimer();
            if (startCooldownUntilMs !== 0) {
                startCooldownUntilMs = 0;
                if (cachedMetricsSnapshot) {
                    applyMetricsData(cachedMetricsSnapshot, { fromCache: true });
                }
            }
            return;
        }
        const remainingMs = Math.max(0, startCooldownUntilMs - Date.now());
        clearStartCooldownTimer();
        startCooldownTimer = window.setTimeout(syncStartButtonCooldown, remainingMs + 20);
    }

    function startStartButtonCooldown() {
        startCooldownUntilMs = Date.now() + START_BUTTON_COOLDOWN_MS;
        syncStartButtonCooldown();
    }

    function clearQueuedStartState() {
        queuedStartPending = false;
    }

    function beginQueuedStartState() {
        queuedStartPending = true;
        startStartButtonCooldown();
        if (cachedMetricsSnapshot) {
            applyMetricsData(cachedMetricsSnapshot, { fromCache: true });
            return;
        }
        const service = document.getElementById("service-status");
        if (service) {
            service.textContent = "Queued";
            service.className = "stat-yellow";
        }
        const startBtn = document.getElementById("start-btn");
        if (startBtn) {
            startBtn.disabled = true;
        }
    }

    function openSudoModal(form) {
        pendingSudoForm = form;
        const modal = document.getElementById("sudo-modal");
        const input = document.getElementById("sudo-modal-input");
        const title = document.getElementById("sudo-modal-title");
        const text = document.getElementById("sudo-modal-text");
        const image = document.getElementById("sudo-modal-image");
        const errorText = document.getElementById("sudo-modal-error");
        if (!modal || !input) return;
        if (title) title.textContent = "Password Required";
        if (text) text.textContent = "Enter sudo password to continue.";
        if (image) image.hidden = true;
        if (errorText) {
            errorText.textContent = "";
            errorText.hidden = true;
        }
        input.value = "";
        modal.setAttribute("aria-hidden", "false");
        modal.classList.add("open");
        input.focus();
    }

    function closeSudoModal() {
        const modal = document.getElementById("sudo-modal");
        const input = document.getElementById("sudo-modal-input");
        const title = document.getElementById("sudo-modal-title");
        const text = document.getElementById("sudo-modal-text");
        const image = document.getElementById("sudo-modal-image");
        const errorText = document.getElementById("sudo-modal-error");
        if (modal) {
            modal.classList.remove("open");
            modal.setAttribute("aria-hidden", "true");
            modal.style.display = "none";
            // Force a reflow so next open state is applied cleanly.
            void modal.offsetHeight;
            modal.style.display = "";
        }
        if (input) input.value = "";
        if (title) title.textContent = "Password Required";
        if (text) text.textContent = "Enter sudo password to continue.";
        if (image) image.hidden = true;
        if (errorText) {
            errorText.textContent = "";
            errorText.hidden = true;
        }
        pendingSudoForm = null;
    }

    function showSudoModalError(message) {
        const modal = document.getElementById("sudo-modal");
        const input = document.getElementById("sudo-modal-input");
        const title = document.getElementById("sudo-modal-title");
        const text = document.getElementById("sudo-modal-text");
        const image = document.getElementById("sudo-modal-image");
        const errorText = document.getElementById("sudo-modal-error");
        if (!modal || !input) return;
        if (title) title.textContent = "Action Rejected";
        if (text) text.textContent = "Password incorrect. Whatever you were trying to do is cancelled.";
        if (image) image.hidden = false;
        if (errorText) {
            errorText.textContent = message || "Password incorrect.";
            errorText.hidden = false;
        }
        modal.setAttribute("aria-hidden", "false");
        modal.classList.add("open");
        input.focus();
        input.select();
    }

    function showSuccessModal(message) {
        closeSudoModal();
        const modal = document.getElementById("success-modal");
        const text = document.getElementById("success-modal-text");
        if (!modal || !text) return;
        text.textContent = message || "Action completed successfully.";
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

    function summarizeSuccess(action, payload = {}) {
        const message = String(payload && payload.message ? payload.message : "").trim();
        if (message) return message;
        if (action === "/start") return "Server start request accepted.";
        if (action === "/stop") return "Server stop request accepted.";
        if (action === "/backup") return "Backup started.";
        if (action === "/rcon") return "Command submitted successfully.";
        return "Action completed successfully.";
    }

    function closeErrorModal() {
        const modal = document.getElementById("error-modal");
        const details = document.getElementById("error-modal-details");
        const moreBtn = document.getElementById("error-modal-more");
        if (!modal) return;
        modal.classList.remove("open");
        modal.setAttribute("aria-hidden", "true");
        if (details) {
            details.hidden = true;
        }
        if (moreBtn) {
            moreBtn.textContent = "Show more";
        }
    }


    function stopOperationPoll(opId) {
        const key = String(opId || "").trim();
        if (!key) return;
        const timerId = operationPollTimers[key];
        if (!timerId) return;
        clearTimeout(timerId);
        delete operationPollTimers[key];
    }

    async function pollOperationStatus(opId, action) {
        const key = String(opId || "").trim();
        if (!key) return;
        let response;
        let payload = null;
        try {
            response = await fetch(`/operation-status/${encodeURIComponent(key)}`, {
                method: "GET",
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                },
                cache: "no-store",
            });
        } catch (_) {
            operationPollTimers[key] = window.setTimeout(() => pollOperationStatus(key, action), 1000);
            return;
        }
        try {
            payload = await response.json();
        } catch (_) {
            payload = null;
        }
        if (!response.ok || !payload || payload.ok === false || !payload.operation) {
            operationPollTimers[key] = window.setTimeout(() => pollOperationStatus(key, action), 1200);
            return;
        }
        const operation = payload.operation || {};
        const status = String(operation.status || "").trim().toLowerCase();
        if (status === "observed") {
            stopOperationPoll(key);
            if (action === "/backup") {
                setBackupStatusOverride("");
                announceBackupsListInvalidation();
            }
            return;
        }
        if (status === "failed") {
            stopOperationPoll(key);
            if (action === "/start") {
                clearQueuedStartState();
            }
            if (action === "/backup") {
                setBackupStatusOverride("");
            }
            showErrorModal(
                String(operation.message || "Action failed."),
                {
                    errorCode: String(operation.error_code || "operation_failed"),
                    action,
                }
            );
            return;
        }
        if (status === "intent" || status === "in_progress") {
            if (action === "/backup") {
                setBackupStatusOverride(status === "in_progress" ? "Running" : "Queued");
            }
        }
        operationPollTimers[key] = window.setTimeout(() => pollOperationStatus(key, action), 700);
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
        const errorPayload = {
            error_code: code || "",
            action: action || "",
            message: message || "",
        };
        if (http) {
            http.postJson("/ui-error-log", errorPayload, {
                csrfToken,
                headers: { "X-Requested-With": "XMLHttpRequest" },
            }).catch(() => {});
        } else {
            fetch("/ui-error-log", {
                method: "POST",
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                    "X-CSRF-Token": csrfToken || "",
                    "Content-Type": "application/json",
                },
                body: JSON.stringify(errorPayload),
                keepalive: true,
                cache: "no-store",
            }).catch(() => {});
        }
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

    function applyMetricsData(data, options = {}) {
        if (!data) return;
        const fromCache = options.fromCache === true;
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
            setIdleCountdownFromParsedSeconds(homeTimeUtils.parseCountdown(data.idle_countdown));
            if (idleCountdown && idleCountdownSeconds === null) idleCountdown.textContent = data.idle_countdown;
        }
        if (sessionDuration && data.session_duration !== undefined) {
            setSessionDurationFromParsedSeconds(homeTimeUtils.parseSessionDuration(data.session_duration));
            if (sessionDurationSeconds !== null) {
                sessionDuration.textContent = homeTimeUtils.formatSessionDuration(sessionDurationSeconds);
            } else {
                sessionDuration.textContent = data.session_duration;
            }
        }
        let backupStatusText = data.backup_status;
        let backupStatusClass = data.backup_status_class;
        if (backupStatusOverride === "Queued") {
            backupStatusText = "Queued";
            backupStatusClass = "stat-yellow";
        } else if (backupStatusOverride === "Running") {
            backupStatusText = "Running";
            backupStatusClass = "stat-green";
        }
        if (backupStatus && backupStatusText) backupStatus.textContent = backupStatusText;
        if (backupStatus && backupStatusClass) backupStatus.className = backupStatusClass;
        if (lastBackup && data.last_backup_time) lastBackup.textContent = data.last_backup_time;
        if (nextBackup && data.next_backup_time) nextBackup.textContent = data.next_backup_time;
        if (backupsStatus && data.backups_status) backupsStatus.textContent = data.backups_status;
        let serviceStatusText = data.service_status;
        let serviceStatusClass = data.service_status_class;
        const observedServiceState = String(serviceStatusText || "").trim().toLowerCase();
        if (queuedStartPending) {
            if (observedServiceState === "starting" || observedServiceState === "running" || data.service_running_status === "active") {
                clearQueuedStartState();
                serviceStatusText = data.service_status;
                serviceStatusClass = data.service_status_class;
            } else {
                serviceStatusText = "Queued";
                serviceStatusClass = "stat-yellow";
            }
        }
        if (service && serviceStatusText) service.textContent = serviceStatusText;
        if (service && serviceStatusClass) service.className = serviceStatusClass;
        if (serverTime && (data.server_time || data.server_time_epoch_ms)) {
            const accepted = setServerTimeFromEpoch(data.server_time_epoch_ms, data.server_time_zone)
                || setServerTimeFromText(data.server_time);
            if (accepted) {
                tickServerClock();
            } else {
                serverTime.textContent = data.server_time;
            }
        }
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
        sessionDurationRunning = isServiceRunningInMetrics(data);
        if (!sessionDurationRunning && (!data.session_duration || data.session_duration === "--")) {
            setSessionDurationFromParsedSeconds(null);
        }
        const rconEnabled = data.rcon_enabled === true;
        const lowStorageBlocked = data.low_storage_blocked === true;
        const lowStorageMessage = (data.low_storage_message || "").trim();
        const backupWarningSeq = Number(data.backup_warning_seq || 0);
        const backupWarningMessage = (data.backup_warning_message || "").trim();
        const serviceStateLabel = String(serviceStatusText || data.service_status || "").trim().toLowerCase();
        const serviceIsOff = serviceStateLabel === "off";
        const serviceIsStarting = serviceStateLabel === "starting";
        const serviceIsShutting = serviceStateLabel === "shutting down";
        const allowStart = serviceIsOff && !lowStorageBlocked && !isStartCooldownActive();
        const allowStop = !serviceIsOff;

        if (startBtn) startBtn.disabled = !allowStart;
        if (stopBtn) stopBtn.disabled = !allowStop;

        if (data.service_running_status === "active") {
            if (rconInput) rconInput.disabled = !rconEnabled;
            if (rconSubmit) rconSubmit.disabled = !rconEnabled;
            if (rconInput) {
                rconInput.placeholder = rconEnabled
                    ? "Enter Minecraft server command"
                    : "RCON unavailable (missing rcon.password)";
            }
        } else {
            if (rconInput) {
                rconInput.disabled = true;
                rconInput.placeholder = serviceIsOff
                    ? "Server is off"
                    : "Loading server state...";
            }
            if (rconSubmit) rconSubmit.disabled = true;
        }
        if (!fromCache && lowStorageBlocked) {
            if (!lowStorageModalShown && lowStorageMessage) {
                showErrorModal(lowStorageMessage, {
                    errorCode: "low_storage_space",
                    action: window.location.pathname || "/",
                });
                lowStorageModalShown = true;
            }
        } else if (!fromCache) {
            lowStorageModalShown = false;
        }
        if (!fromCache && backupWarningMessage && backupWarningSeq > lastBackupWarningSeq) {
            showErrorModal(backupWarningMessage, {
                errorCode: "backup_warning",
                action: "/backup",
            });
            lastBackupWarningSeq = backupWarningSeq;
        } else if (!fromCache && backupWarningSeq > lastBackupWarningSeq) {
            lastBackupWarningSeq = backupWarningSeq;
        }
        if (backupBtn) {
            const backupBusy = backupStatusText === "Running" || backupStatusText === "Queued";
            const backupBlocked = serviceIsStarting || serviceIsShutting || lowStorageBlocked || backupBusy;
            backupBtn.disabled = backupBlocked;
        }
        cachedMetricsSnapshot = data;
        applyRefreshMode(data.service_status);
    }

    async function submitFormAjax(form, sudoPassword = undefined) {
        if (!form) return;
        const action = form.getAttribute("action") || "/";
        const method = (form.getAttribute("method") || "POST").toUpperCase();
        if (action === "/backup") {
            setBackupStatusOverride("Queued");
        }
        const formData = new FormData(form);
        if (sudoPassword !== undefined) {
            formData.set("sudo_password", sudoPassword);
        }
        try {
            const result = http
                ? await http.postForm(action, formData, {
                    csrfToken,
                    headers: { "X-Requested-With": "XMLHttpRequest" },
                })
                : {
                    response: await fetch(action, {
                        method,
                        body: formData,
                        headers: {
                            "X-Requested-With": "XMLHttpRequest",
                            Accept: "application/json",
                            "X-CSRF-Token": csrfToken,
                        },
                    }),
                    payload: {},
                };
            const response = result.response;
            let payload = result.payload;
            if (!http) {
                try {
                    payload = await response.json();
                } catch (_) {
                    payload = {};
                }
            }

            if (!response.ok || payload.ok === false) {
                const message = (payload && payload.message) ? payload.message : "Action rejected.";
                const isPasswordRejected =
                    payload &&
                    payload.error === "password_incorrect" &&
                    (action === "/stop" || action === "/rcon");
                if (action === "/start") {
                    clearQueuedStartState();
                }
                if (action === "/backup") {
                    setBackupStatusOverride("");
                }
                if (isPasswordRejected) {
                    showSudoModalError(message);
                } else {
                    showErrorModal(message, {
                        errorCode: payload && payload.error ? String(payload.error) : "",
                        action,
                    });
                }
                return;
            }

            showSuccessModal(summarizeSuccess(action, payload));
            if (payload && payload.accepted === true && payload.op_id) {
                if (action === "/start") {
                    beginQueuedStartState();
                }
                const opId = String(payload.op_id || "").trim();
                if (opId) {
                    stopOperationPoll(opId);
                    operationPollTimers[opId] = window.setTimeout(() => pollOperationStatus(opId, action), 400);
                }
                return;
            }
        } catch (err) {
            if (action === "/start") {
                clearQueuedStartState();
            }
            if (action === "/backup") {
                setBackupStatusOverride("");
            }
            showErrorModal("Network request failed.", {
                errorCode: "network_error",
                action,
            });
        }
    }

    function clearRefreshTimers() {
        // Prevent duplicate interval loops when switching modes.
        if (countdownTimer) {
            clearTimeout(countdownTimer);
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
            sessionDurationRunning = false;
            return;
        }

        // In Active mode, locally simulate countdown/session duration.
        scheduleRuntimeSimulationTick();
    }

    // Tear down page-local timers/listeners when the shell unmounts Home.
    function teardownRealtimeConnections() {
        if (homeLogController) {
            homeLogController.teardown();
        }
        if (typeof homeMetricsUnsubscribe === "function") {
            homeMetricsUnsubscribe();
            homeMetricsUnsubscribe = null;
        }
        if (typeof homeLogsUnsubscribe === "function") {
            homeLogsUnsubscribe();
            homeLogsUnsubscribe = null;
        }
        Object.keys(operationPollTimers).forEach((opId) => stopOperationPoll(opId));
        homeHeartbeatController.stop();
        if (typeof logScrollbarCleanup === "function") {
            logScrollbarCleanup();
            logScrollbarCleanup = null;
        }
        clearRefreshTimers();
        clearServerClockTimer();
        clearStartCooldownTimer();
        refreshMode = null;
    }

    function handleVisibilityRefreshMode() {
        if (document.hidden) {
            if (homeLogController && (!shell || typeof shell.activateHomeLogStream !== "function")) {
                homeLogController.teardown();
            }
            homeHeartbeatController.stop();
            clearServerClockTimer();
            return;
        }
        activateLogStream(selectedLogSource);
        scheduleServerClockTick();
    }

    async function startHomePage() {
        homeCleanup = createCleanupStack();
        const addScopedListener = homeCleanup && typeof homeCleanup.listen === "function"
            ? homeCleanup.listen
            : (target, type, handler, options) => {
                if (!target || typeof target.addEventListener !== "function") return;
                target.addEventListener(type, handler, options);
            };
        document.querySelectorAll("form.ajax-form:not(.sudo-form)").forEach((form) => {
            addScopedListener(form, "submit", async (event) => {
                event.preventDefault();
                await submitFormAjax(form);
            });
        });

        document.querySelectorAll("form.sudo-form").forEach((form) => {
            addScopedListener(form, "submit", async (event) => {
                event.preventDefault();
                if (!passwordRequired) {
                    await submitFormAjax(form);
                    return;
                }
                openSudoModal(form);
            });
        });

        const modalCancel = document.getElementById("sudo-modal-cancel");
        const modalSubmit = document.getElementById("sudo-modal-submit");
        const modalInput = document.getElementById("sudo-modal-input");
        if (modalCancel) {
            addScopedListener(modalCancel, "click", () => closeSudoModal());
        }
        if (modalSubmit) {
            addScopedListener(modalSubmit, "click", async () => {
                if (!pendingSudoForm || !modalInput) return;
                const password = (modalInput.value || "").trim();
                if (!password) return;
                const form = pendingSudoForm;
                await submitFormAjax(form, password);
            });
        }
        if (modalInput) {
            addScopedListener(modalInput, "keydown", (event) => {
                if (event.key === "Enter" && modalSubmit) {
                    event.preventDefault();
                    modalSubmit.click();
                }
            });
        }

        const successOk = document.getElementById("success-modal-ok");
        const successModal = document.getElementById("success-modal");
        if (successModal) {
            addScopedListener(successModal, "click", (event) => {
                if (event.target !== successModal) return;
                successModal.classList.remove("open");
                successModal.setAttribute("aria-hidden", "true");
            });
        }
        if (successOk) {
            addScopedListener(successOk, "click", () => {
                const modal = document.getElementById("success-modal");
                if (modal) {
                    modal.classList.remove("open");
                    modal.setAttribute("aria-hidden", "true");
                }
            });
        }
        const errorOk = document.getElementById("error-modal-ok");
        const errorMore = document.getElementById("error-modal-more");
        const errorModal = document.getElementById("error-modal");
        if (errorModal) {
            addScopedListener(errorModal, "click", (event) => {
                if (event.target !== errorModal) return;
                closeErrorModal();
            });
        }
        if (errorOk) {
            addScopedListener(errorOk, "click", () => {
                closeErrorModal();
            });
        }
        if (errorMore) {
            addScopedListener(errorMore, "click", () => {
                const details = document.getElementById("error-modal-details");
                if (!details) return;
                const nextHidden = !details.hidden;
                details.hidden = nextHidden;
                errorMore.textContent = nextHidden ? "Show more" : "Show less";
            });
        }
        if (alertMessage) {
            if (alertMessageCode === "password_incorrect") {
                showSudoModalError(alertMessage);
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
            setIdleCountdownFromParsedSeconds(homeTimeUtils.parseCountdown(idleCountdown.textContent.trim()));
        }
        const existingSessionDuration = document.getElementById("session-duration");
        if (existingSessionDuration) {
            setSessionDurationFromParsedSeconds(homeTimeUtils.parseSessionDuration(existingSessionDuration.textContent.trim()));
        }
        const existingServerTime = document.getElementById("server-time");
        if (existingServerTime) {
            setServerTimeFromText(existingServerTime.textContent.trim(), { force: true });
        }
        const logSource = document.getElementById("log-source");
        if (shell && typeof shell.subscribeHomeLogs === "function") {
            homeLogsUnsubscribe = shell.subscribeHomeLogs((source, lines) => {
                if (!LOG_SOURCE_KEYS.includes(source)) return;
                syncHomeLogSource(source, lines);
            });
            if (homeLogController) {
                homeLogController.hydrateFromShell();
            }
        }
        if (logSource && LOG_SOURCE_KEYS.includes(selectedLogSource)) {
            logSource.value = selectedLogSource;
        }
        if (logSource) {
            addScopedListener(logSource, "change", async () => {
                selectedLogSource = getLogSource();
                if (homeLogController) {
                    selectedLogSource = homeLogController.setSelectedSource(selectedLogSource);
                    logAutoScrollEnabled = homeLogController.getAutoScrollEnabled();
                }
                activateLogStream(selectedLogSource);
                if (homeLogController && !homeLogController.sourceHasEntries(selectedLogSource)) {
                    await loadLogSourceFromServer(selectedLogSource);
                }
                renderActiveLog();
                if (logAutoScrollEnabled) {
                    scrollLogToBottom();
                }
            });
        }
        const existingLog = document.getElementById("minecraft-log");
        if (existingLog && homeLogController) {
            logScrollbarCleanup = homeLogController.bindLogElement(existingLog);
        }
        selectedLogSource = getLogSource();
        if (homeLogController && !homeLogController.sourceHasEntries("minecraft")) {
            setSourceLogText("minecraft", existingLog ? existingLog.textContent : "");
        }
        if (existingLog) {
            renderActiveLog();
            scrollLogToBottom();
        }
        loadDeviceNameMap();
        activateLogStream(selectedLogSource);
        if (homeLogController && !homeLogController.sourceHasEntries(selectedLogSource)) {
            loadLogSourceFromServer(selectedLogSource);
        }
        if (shell && typeof shell.subscribeMetrics === "function") {
            homeMetricsUnsubscribe = shell.subscribeMetrics((payload) => {
                if (payload && typeof payload === "object") {
                    applyMetricsData(payload);
                }
            });
        }
        if (window.__MCWEB_LAST_METRICS_SNAPSHOT && typeof window.__MCWEB_LAST_METRICS_SNAPSHOT === "object") {
            applyMetricsData(window.__MCWEB_LAST_METRICS_SNAPSHOT);
        }
        scheduleServerClockTick();
        const service = document.getElementById("service-status");
        applyRefreshMode(service ? service.textContent : "");
        homeHeartbeatController.start();
        addScopedListener(document, "visibilitychange", handleVisibilityRefreshMode);
        addScopedListener(window, "pagehide", teardownRealtimeConnections);
        addScopedListener(window, "beforeunload", teardownRealtimeConnections);
        teardownHomePage = () => {
            teardownRealtimeConnections();
            if (homeCleanup && typeof homeCleanup.run === "function") {
                homeCleanup.run();
                homeCleanup = null;
            }
        };
    }

    function mountHomePage() {
        if (typeof teardownHomePage === "function") {
            try {
                teardownHomePage();
            } catch (_) {
                // Ignore stale home teardown failures before remounting.
            }
        }
        return startHomePage();
    }

    if (pageModules && typeof pageModules.register === "function") {
        pageModules.register("home", {
            mount: mountHomePage,
            unmount: function () {
                if (typeof teardownHomePage === "function") {
                    teardownHomePage();
                }
            },
        });
    }

    if (!document.getElementById("mcweb-app-content")) {
        mountHomePage();
    }
})();














