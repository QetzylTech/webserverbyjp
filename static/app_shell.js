(function () {
    const contentRoot = document.getElementById("mcweb-app-content");
    if (!contentRoot) return;

    const FRAGMENT_HEADER = "X-MCWEB-Fragment";
    const shellPaths = new Set(["/", "/readme", "/backups", "/crash-logs", "/minecraft-logs", "/maintenance", "/panel-settings"]);
    const CACHE_TTL_MS = 5 * 60 * 1000;
    const README_DEFAULT_PATH = "/doc/server_setup_doc.md";
    const HOME_LOG_LIMITS = {
        minecraft: 500,
        backup: 200,
        mcweb: 200,
        mcweb_log: 200,
    };
    const HOME_LOG_PATHS = {
        minecraft: "/log-stream/minecraft",
        backup: "/log-stream/backup",
        mcweb: "/log-stream/mcweb",
        mcweb_log: "/log-stream/mcweb_log",
    };
    const CHIME_SOUNDS = {
        startup: "https://cdn.jsdelivr.net/gh/Calinou/kenney-interface-sounds@master/addons/kenney_interface_sounds/maximize_008.wav",
        shutdown: "https://cdn.jsdelivr.net/gh/Calinou/kenney-interface-sounds@master/addons/kenney_interface_sounds/minimize_008.wav",
        error: "https://cdn.jsdelivr.net/gh/Calinou/kenney-interface-sounds@master/addons/kenney_interface_sounds/question_002.wav",
    };
    const MULTITAB_CHANNEL = "mcweb.data";
    const PRIMARY_STORAGE_KEY = "mcweb.primaryTab";
    const VIEW_STATE_STORAGE_KEY = "mcweb.viewState.v1";
    const PRIMARY_TTL_MS = 6000;
    const PRIMARY_HEARTBEAT_MS = 2000;

    let currentPath = window.location.pathname;
    let navigationToken = 0;
    let navigationController = null;
    let navBound = false;
    let themeBound = false;
    let metricsEventSource = null;
    let notificationsEventSource = null;
    let pendingPromptAction = null;
    let isPrimaryTab = false;
    let primaryHeartbeatTimer = null;
    let primaryCheckTimer = null;
    const loadedScriptUrls = new Set();
    const loadingScriptPromises = new Map();
    const pageModules = window.MCWebPageModules || {
        register: function () {},
        mount: function () { return null; },
        unmount: function () {},
    };


    // Long-lived shell-owned runtime state. Page modules should keep only
    // transient DOM/view state locally and read shared data through this layer.
    function createDefaultHomeViewState() {
        return {
            selectedLogSource: "minecraft",
            logAutoScrollBySource: {
                minecraft: true,
                backup: true,
                mcweb: true,
                mcweb_log: true,
            },
            logScrollTopBySource: {
                minecraft: 0,
                backup: 0,
                mcweb: 0,
                mcweb_log: 0,
            },
        };
    }

    function createDefaultFilePageViewState() {
        return {
            sortMode: "newest",
            backupSortMode: "newest",
            backupFilters: null,
            currentLogFileSource: "",
            activeLogSource: "",
            activeViewedFilename: "",
            activeRestoreFilename: "",
            restoreLogFilename: "",
            restoreLogLines: [],
            restoreLogScrollTop: 0,
            viewerOpen: false,
            viewerKind: "",
            viewerRequest: null,
            viewerScrollTop: 0,
            listScrollTop: 0,
        };
    }

    function createDefaultMaintenanceViewState() {
        return {
            currentScope: "backups",
            currentActionView: "rules",
            historyViewMode: "successful",
        };
    }

    function createDefaultDocsViewState() {
        return {
            scrollByUrl: {},
        };
    }

    function readPersistedViewState() {
        try {
            const raw = window.localStorage.getItem(VIEW_STATE_STORAGE_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== "object") return null;
            return parsed;
        } catch (_) {
            return null;
        }
    }

    function writePersistedViewState(payload) {
        try {
            window.localStorage.setItem(VIEW_STATE_STORAGE_KEY, JSON.stringify(payload || {}));
        } catch (_) {
            // Ignore storage failures.
        }
    }

    function persistViewState() {
        writePersistedViewState({
            homeView: shellState.homeView,
            fileViews: shellState.fileViews,
            maintenanceView: shellState.maintenanceView,
            docsView: shellState.docsView,
        });
    }

    const shellState = {
        metricsSnapshot: window.__MCWEB_LAST_METRICS_SNAPSHOT || null,
        lastServiceStatus: "",
        deviceMapEntry: null,
        readmeUrlEntry: null,
        readmeUrlPromise: null,
        readmeTextCache: new Map(),
        readmeTextPromises: new Map(),
        deviceMapPromise: null,
        filePageListCache: new Map(),
        filePageListPromises: new Map(),
        logFileListCache: new Map(),
        logFileListPromises: new Map(),
        logTextCache: new Map(),
        logTextPromises: new Map(),
        viewedFileCache: new Map(),
        viewedFilePromises: new Map(),
        maintenanceStateCache: new Map(),
        maintenanceStatePromises: new Map(),
        metricsSubscribers: new Set(),
        homeLogSubscribers: new Set(),
        homeView: createDefaultHomeViewState(),
        fileViews: {
            backups: createDefaultFilePageViewState(),
            crash_logs: createDefaultFilePageViewState(),
            minecraft_logs: createDefaultFilePageViewState(),
        },
        maintenanceView: createDefaultMaintenanceViewState(),
        docsView: createDefaultDocsViewState(),
        homeLogs: {
            buffers: {
                minecraft: [],
                backup: [],
                mcweb: [],
                mcweb_log: [],
            },
            pending: {
                minecraft: [],
                backup: [],
                mcweb: [],
                mcweb_log: [],
            },
            flushTimers: {
                minecraft: null,
                backup: null,
                mcweb: null,
                mcweb_log: null,
            },
            streams: {
                minecraft: null,
                backup: null,
                mcweb: null,
                mcweb_log: null,
            },
            activeSource: "",
        },
    };
    const persistedViewState = readPersistedViewState();
    if (persistedViewState && typeof persistedViewState === "object") {
        if (persistedViewState.homeView && typeof persistedViewState.homeView === "object") {
            Object.assign(shellState.homeView, persistedViewState.homeView);
        }
        if (persistedViewState.fileViews && typeof persistedViewState.fileViews === "object") {
            Object.keys(shellState.fileViews).forEach((key) => {
                const stored = persistedViewState.fileViews[key];
                if (stored && typeof stored === "object") {
                    Object.assign(shellState.fileViews[key], stored);
                }
            });
        }
        if (persistedViewState.maintenanceView && typeof persistedViewState.maintenanceView === "object") {
            Object.assign(shellState.maintenanceView, persistedViewState.maintenanceView);
        }
        if (persistedViewState.docsView && typeof persistedViewState.docsView === "object") {
            Object.assign(shellState.docsView, persistedViewState.docsView);
        }
    }
    const soundState = {
        unlocked: false,
        unlockAttempted: false,
        audioByKey: {},
    };

    const tabId = (() => {
        try {
            const existing = window.sessionStorage.getItem("mcweb.tabId");
            if (existing) return existing;
            const generated = (window.crypto && typeof window.crypto.randomUUID === "function")
                ? window.crypto.randomUUID()
                : `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
            window.sessionStorage.setItem("mcweb.tabId", generated);
            return generated;
        } catch (_) {
            return `tab-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
        }
    })();

    const broadcast = (() => {
        let channel = null;
        const listeners = new Set();
        if (typeof BroadcastChannel === "function") {
            channel = new BroadcastChannel(MULTITAB_CHANNEL);
            channel.onmessage = (event) => {
                listeners.forEach((listener) => listener(event.data));
            };
        } else if (window.addEventListener) {
            window.addEventListener("storage", (event) => {
                if (event.key !== `${MULTITAB_CHANNEL}:fallback` || !event.newValue) return;
                try {
                    const payload = JSON.parse(event.newValue);
                    listeners.forEach((listener) => listener(payload));
                } catch (_) {
                    // Ignore malformed payloads.
                }
            });
        }
        return {
            send: (payload) => {
                const message = Object.assign({}, payload, { origin: tabId, sentAt: Date.now() });
                if (channel) {
                    channel.postMessage(message);
                    return;
                }
                try {
                    window.localStorage.setItem(`${MULTITAB_CHANNEL}:fallback`, JSON.stringify(message));
                } catch (_) {
                    // Ignore storage failures.
                }
            },
            onMessage: (listener) => {
                if (typeof listener !== "function") return () => {};
                listeners.add(listener);
                return () => listeners.delete(listener);
            },
        };
    })();

    function shouldPlaySoundHere() {
        return document.visibilityState === "visible";
    }

    function createSoundElement(src) {
        const audio = new Audio();
        audio.preload = "auto";
        audio.src = src;
        audio.crossOrigin = "anonymous";
        return audio;
    }

    function ensureSoundBank() {
        Object.keys(CHIME_SOUNDS).forEach((key) => {
            if (soundState.audioByKey[key]) return;
            soundState.audioByKey[key] = createSoundElement(CHIME_SOUNDS[key]);
        });
    }

    function attemptUnlockSounds() {
        if (soundState.unlocked || soundState.unlockAttempted) return;
        soundState.unlockAttempted = true;
        ensureSoundBank();
        const audio = soundState.audioByKey.startup;
        if (!audio) return;
        audio.muted = true;
        const playAttempt = audio.play();
        if (playAttempt && typeof playAttempt.then === "function") {
            playAttempt.then(() => {
                audio.pause();
                audio.currentTime = 0;
                audio.muted = false;
                soundState.unlocked = true;
            }).catch(() => {
                audio.muted = false;
            });
        } else {
            audio.muted = false;
        }
    }

    function bindSoundUnlock() {
        const unlock = () => {
            attemptUnlockSounds();
            document.removeEventListener("pointerdown", unlock);
            document.removeEventListener("keydown", unlock);
        };
        document.addEventListener("pointerdown", unlock, { once: true });
        document.addEventListener("keydown", unlock, { once: true });
    }

    function playSound(key) {
        ensureSoundBank();
        if (!shouldPlaySoundHere()) return;
        const audio = soundState.audioByKey[key];
        if (!audio) return;
        if (!soundState.unlocked) {
            attemptUnlockSounds();
            return;
        }
        try {
            audio.currentTime = 0;
            const result = audio.play();
            if (result && typeof result.catch === "function") {
                result.catch(() => {});
            }
        } catch (_) {
            // Ignore playback failures (autoplay restrictions).
        }
    }

    function emitSoundEvent(key) {
        if (isPrimaryTab) {
            broadcast.send({ type: "sound_event", sound: key });
        }
        playSound(key);
    }

    function readPrimaryRecord() {
        try {
            const raw = window.localStorage.getItem(PRIMARY_STORAGE_KEY);
            if (!raw) return null;
            const parsed = JSON.parse(raw);
            if (!parsed || typeof parsed !== "object") return null;
            return parsed;
        } catch (_) {
            return null;
        }
    }

    function writePrimaryRecord() {
        try {
            window.localStorage.setItem(
                PRIMARY_STORAGE_KEY,
                JSON.stringify({ id: tabId, ts: Date.now() })
            );
        } catch (_) {
            // Ignore storage failures.
        }
    }

    function primaryRecordActive(record) {
        if (!record || typeof record !== "object") return false;
        const ts = Number(record.ts || 0);
        if (!Number.isFinite(ts)) return false;
        return (Date.now() - ts) < PRIMARY_TTL_MS;
    }

    function startPrimaryHeartbeat() {
        if (primaryHeartbeatTimer) return;
        writePrimaryRecord();
        broadcast.send({ type: "primary_heartbeat", primaryId: tabId });
        primaryHeartbeatTimer = window.setInterval(() => {
            writePrimaryRecord();
            broadcast.send({ type: "primary_heartbeat", primaryId: tabId });
        }, PRIMARY_HEARTBEAT_MS);
    }

    function stopPrimaryHeartbeat() {
        if (!primaryHeartbeatTimer) return;
        window.clearInterval(primaryHeartbeatTimer);
        primaryHeartbeatTimer = null;
    }

    function startPrimaryStreams() {
        startMetricsStream();
        startNotificationsStream();
        if (shellState.homeLogs.activeSource) {
            ensureHomeLogStreamStarted(shellState.homeLogs.activeSource);
        }
    }

    function stopPrimaryStreams() {
        stopMetricsStream();
        stopNotificationsStream();
        stopAllHomeLogStreams();
    }

    function setPrimaryState(nextIsPrimary) {
        if (nextIsPrimary === isPrimaryTab) return;
        isPrimaryTab = nextIsPrimary;
        if (isPrimaryTab) {
            startPrimaryHeartbeat();
            startPrimaryStreams();
            broadcast.send({ type: "primary_active", primaryId: tabId });
        } else {
            stopPrimaryHeartbeat();
            stopPrimaryStreams();
        }
    }

    function evaluatePrimaryRole() {
        const record = readPrimaryRecord();
        if (primaryRecordActive(record) && record.id !== tabId) {
            setPrimaryState(false);
            return;
        }
        setPrimaryState(true);
    }

    function startPrimaryElection() {
        evaluatePrimaryRole();
        if (primaryCheckTimer) return;
        primaryCheckTimer = window.setInterval(() => {
            evaluatePrimaryRole();
        }, PRIMARY_HEARTBEAT_MS);
    }

    function broadcastStateSnapshot(targetId = "") {
        if (!isPrimaryTab) return;
        broadcast.send({
            type: "state_snapshot",
            targetId,
            payload: {
                metricsSnapshot: shellState.metricsSnapshot || null,
                homeLogs: {
                    buffers: shellState.homeLogs.buffers,
                },
            },
        });
    }

    broadcast.onMessage((message) => {
        if (!message || typeof message !== "object") return;
        if (message.origin === tabId) return;
        const type = String(message.type || "");
        if (type === "primary_heartbeat") {
            const record = readPrimaryRecord();
            if (record && record.id !== tabId) {
                setPrimaryState(false);
            }
            return;
        }
        if (type === "primary_active") {
            if (message.primaryId && message.primaryId !== tabId) {
                setPrimaryState(false);
            }
            return;
        }
        if (type === "request_state") {
            if (isPrimaryTab) {
                broadcastStateSnapshot(message.origin || "");
            }
            return;
        }
        if (type === "request_log_source") {
            if (isPrimaryTab && message.source) {
                activateHomeLogStream(String(message.source || ""));
                broadcastStateSnapshot(message.origin || "");
            }
            return;
        }
        if (type === "state_snapshot") {
            if (message.targetId && message.targetId !== tabId) return;
            const payload = message.payload || {};
            if (payload.metricsSnapshot) {
                dispatchMetricsSnapshot(payload.metricsSnapshot, { fromBroadcast: true });
            }
            const logs = payload.homeLogs && payload.homeLogs.buffers ? payload.homeLogs.buffers : null;
            if (logs && typeof logs === "object") {
                Object.keys(logs).forEach((key) => {
                    const lines = Array.isArray(logs[key]) ? logs[key].join("\n") : "";
                    setHomeLogSnapshot(key, lines, { fromBroadcast: true });
                });
            }
            return;
        }
        if (type === "metrics_snapshot" && message.payload) {
            dispatchMetricsSnapshot(message.payload, { fromBroadcast: true });
            return;
        }
        if (type === "log_snapshot" && message.source) {
            const lines = Array.isArray(message.lines) ? message.lines.join("\n") : "";
            setHomeLogSnapshot(message.source, lines, { fromBroadcast: true });
            return;
        }
        if (type === "log_line" && message.source) {
            appendHomeLogLine(message.source, message.line || "", { fromBroadcast: true });
            return;
        }
        if (type === "notification" && message.payload) {
            handleNotificationPayload(message.payload, { fromBroadcast: true });
            return;
        }
        if (type === "sound_event" && message.sound) {
            playSound(String(message.sound || ""));
        }
    });

    function isInternalNavLink(anchor) {
        if (!(anchor instanceof HTMLAnchorElement)) return false;
        if (anchor.target && anchor.target !== "_self") return false;
        if (anchor.hasAttribute("download")) return false;
        const href = anchor.getAttribute("href") || "";
        if (!href || href.startsWith("#")) return false;
        const url = new URL(anchor.href, window.location.href);
        return url.origin === window.location.origin && shellPaths.has(url.pathname);
    }

    function currentPageManifest() {
        return contentRoot.querySelector("#mcweb-page-root");
    }

    function pruneContentRootWhitespace() {
        if (!contentRoot) return;
        const nodes = Array.from(contentRoot.childNodes || []);
        nodes.forEach((node) => {
            if (!node) return;
            if (node.nodeType === 3) {
                if (!String(node.nodeValue || "").trim()) {
                    node.remove();
                }
                return;
            }
            if (node.nodeType === 8) {
                node.remove();
            }
        });
    }

    function parseAssetListFromManifest(manifest, attrName) {
        if (!manifest) return [];
        try {
            const parsed = JSON.parse(manifest.getAttribute(attrName) || "[]");
            return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
        } catch (_) {
            return [];
        }
    }

    function parsePageAssetList(attrName) {
        return parseAssetListFromManifest(currentPageManifest(), attrName);
    }

    function parseFragmentAssets(html, attrName) {
        try {
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, "text/html");
            return parseAssetListFromManifest(doc.querySelector("#mcweb-page-root"), attrName);
        } catch (_) {
            return [];
        }
    }


    function renderCpuPerCoreHtml(items) {
        if (!Array.isArray(items) || items.length === 0) return "Loading...";
        return items.map((item) => {
            const cls = String(item && item.class ? item.class : "").trim();
            const idx = String(item && item.index !== undefined ? item.index : "?");
            const value = String(item && item.value !== undefined ? item.value : "unknown");
            const safeCls = cls ? ` class="${cls}"` : "";
            return `<span${safeCls}>CPU${idx} ${value}%</span>`;
        }).join(" | ");
    }

    function hydrateHomeFragmentHtml(html) {
        const snapshot = shellState.metricsSnapshot;
        if (!snapshot || typeof snapshot !== "object") return html;
        try {
            const parser = new DOMParser();
            const doc = parser.parseFromString(html, "text/html");
            const root = doc.querySelector("#mcweb-page-root");
            if (!root || root.getAttribute("data-page-key") !== "home") {
                return html;
            }

            const setText = (id, value, fallback = null) => {
                const node = doc.getElementById(id);
                if (!node) return;
                if (value !== undefined && value !== null && value !== "") {
                    node.textContent = String(value);
                } else if (fallback !== null) {
                    node.textContent = String(fallback);
                }
            };
            const setClass = (id, cls) => {
                const node = doc.getElementById(id);
                if (!node) return;
                if (cls) {
                    node.className = String(cls);
                } else {
                    node.removeAttribute("class");
                }
            };

            setText("control-panel-title", snapshot.world_name ? `${snapshot.world_name} Control Panel` : "Control Panel");
            setText("server-time", snapshot.server_time, "--");
            setText("ram-usage", snapshot.ram_usage, "unknown");
            setClass("ram-usage", snapshot.ram_usage_class || "");
            const cpu = doc.getElementById("cpu-per-core");
            if (cpu) {
                cpu.innerHTML = renderCpuPerCoreHtml(snapshot.cpu_per_core_items);
            }
            setText("cpu-frequency", snapshot.cpu_frequency, "unknown");
            setClass("cpu-frequency", snapshot.cpu_frequency_class || "");
            setText("storage-usage", snapshot.storage_usage, "unknown");
            setClass("storage-usage", snapshot.storage_usage_class || "");
            setText("service-status", snapshot.service_status, "Off");
            setClass("service-status", snapshot.service_status_class || "");
            setText("players-online", snapshot.players_online, "--");
            setText("tick-rate", snapshot.tick_rate, "--");
            setText("idle-countdown", snapshot.idle_countdown, "--:--");
            setText("backup-status", snapshot.backup_status, "Idle");
            setClass("backup-status", snapshot.backup_status_class || "");
            setText("last-backup-time", snapshot.last_backup_time, "--");
            setText("next-backup-time", snapshot.next_backup_time, "--");
            setText("backups-status", snapshot.backups_status, "unknown");

            const sessionDuration = doc.getElementById("session-duration");
            const durationPrefix = doc.getElementById("service-status-duration-prefix");
            const showDuration = snapshot.service_status === "Running" && snapshot.session_duration && snapshot.session_duration !== "--";
            if (sessionDuration) {
                sessionDuration.textContent = snapshot.session_duration || "--";
                sessionDuration.style.display = showDuration ? "" : "none";
            }
            if (durationPrefix) {
                durationPrefix.textContent = showDuration ? " for " : "";
            }

            const serviceRunning = String(snapshot.service_running_status || "") === "active";
            const serviceStateLabel = String(snapshot.service_status || "").trim().toLowerCase();
            const rconEnabled = snapshot.rcon_enabled === true;
            const lowStorageBlocked = snapshot.low_storage_blocked === true;
            const backupBusy = snapshot.backup_status === "Running" || snapshot.backup_status === "Queued";
            const serviceIsOff = serviceStateLabel === "off";
            const serviceIsStarting = serviceStateLabel === "starting";
            const serviceIsShutting = serviceStateLabel === "shutting down";

            const startBtn = doc.getElementById("start-btn");
            if (startBtn) startBtn.disabled = !(serviceIsOff && !lowStorageBlocked);
            const stopBtn = doc.getElementById("stop-btn");
            if (stopBtn) stopBtn.disabled = serviceIsOff;
            const backupBtn = doc.getElementById("backup-btn");
            if (backupBtn) backupBtn.disabled = backupBusy || serviceIsStarting || serviceIsShutting || lowStorageBlocked;
            const rconInput = doc.getElementById("rcon-command");
            if (rconInput) {
                rconInput.disabled = !(serviceRunning && rconEnabled);
                rconInput.setAttribute(
                    "placeholder",
                    serviceRunning
                        ? (rconEnabled ? "Enter Minecraft server command" : "RCON unavailable (missing rcon.password)")
                        : (serviceStateLabel === "off" ? "Server is off" : "Loading server state...")
                );
            }
            const rconSubmit = doc.getElementById("rcon-submit");
            if (rconSubmit) {
                rconSubmit.disabled = !(serviceRunning && rconEnabled);
            }
            return root.outerHTML;
        } catch (_) {
            return html;
        }
    }

    function syncPageConfig() {
        const page = document.body.dataset.page || "";
        const configEl = contentRoot.querySelector("#mcweb-page-config");
        let payload = {};
        if (configEl) {
            try {
                payload = JSON.parse(configEl.textContent || "{}");
            } catch (_) {
                payload = {};
            }
        }
        delete window.__MCWEB_HOME_CONFIG;
        delete window.__MCWEB_FILES_CONFIG;
        if (page === "home") {
            window.__MCWEB_HOME_CONFIG = payload;
        }
        if (page === "backups" || page === "crash_logs" || page === "minecraft_logs") {
            window.__MCWEB_FILES_CONFIG = payload;
        }
    }

    function routePageKey(pathname) {
        if (pathname === "/") return "home";
        return pathname.replace(/^\//, "").replace(/-/g, "_");
    }

    function setActiveNavForPath(pathname) {
        const pageKey = routePageKey(pathname);
        document.body.dataset.page = pageKey;
        contentRoot.dataset.currentPage = pageKey;
        document.querySelectorAll("#side-nav .nav-link").forEach((link) => {
            const href = link.getAttribute("href") || "";
            if (!href.startsWith("/")) return;
            const isActive = new URL(href, window.location.origin).pathname === pathname;
            link.classList.toggle("active", isActive);
        });
    }

    function createPageStyleLink(href) {
        const link = document.createElement("link");
        link.rel = "stylesheet";
        link.href = href;
        link.setAttribute("data-mcweb-page-style", "1");
        return link;
    }

    function waitForStylesheet(link) {
        if (link.sheet) return Promise.resolve();
        return new Promise((resolve, reject) => {
            const onLoad = () => {
                cleanup();
                resolve();
            };
            const onError = () => {
                cleanup();
                reject(new Error(`Failed to load stylesheet: ${link.href}`));
            };
            const cleanup = () => {
                link.removeEventListener("load", onLoad);
                link.removeEventListener("error", onError);
            };
            link.addEventListener("load", onLoad, { once: true });
            link.addEventListener("error", onError, { once: true });
        });
    }

    async function syncPageStyles(nextStyles) {
        const desired = Array.isArray(nextStyles) ? nextStyles.filter(Boolean) : parsePageAssetList("data-page-styles");
        const existing = new Map(
            Array.from(document.head.querySelectorAll('link[data-mcweb-page-style="1"]')).map((node) => [node.href, node])
        );
        const loadPromises = [];

        desired.forEach((href) => {
            const absoluteHref = new URL(href, window.location.href).href;
            if (existing.has(absoluteHref)) return;
            const link = createPageStyleLink(href);
            document.head.appendChild(link);
            loadPromises.push(waitForStylesheet(link));
        });

        if (loadPromises.length) {
            await Promise.all(loadPromises);
        }

        existing.forEach((node, href) => {
            if (!desired.some((styleHref) => new URL(styleHref, window.location.href).href === href)) {
                node.remove();
            }
        });
    }

    function dispatchSyntheticPageHide() {
        try {
            window.dispatchEvent(new Event("pagehide"));
        } catch (_) {
            // Ignore synthetic lifecycle failures.
        }
    }

    function isLatestNavigationToken(token) {
        return token === navigationToken;
    }

    function loadScriptOnce(src) {
        const absoluteSrc = new URL(src, window.location.href).href;
        if (loadedScriptUrls.has(absoluteSrc)) {
            return Promise.resolve();
        }
        const pending = loadingScriptPromises.get(absoluteSrc);
        if (pending) {
            return pending;
        }
        const promise = new Promise((resolve, reject) => {
            const script = document.createElement("script");
            script.src = absoluteSrc;
            script.async = false;
            script.onload = () => {
                script.remove();
                loadedScriptUrls.add(absoluteSrc);
                loadingScriptPromises.delete(absoluteSrc);
                resolve();
            };
            script.onerror = () => {
                script.remove();
                loadingScriptPromises.delete(absoluteSrc);
                reject(new Error(`Failed to load script: ${src}`));
            };
            document.body.appendChild(script);
        });
        loadingScriptPromises.set(absoluteSrc, promise);
        return promise;
    }

    function loadScriptsSequentially(sources, token) {
        return sources.reduce((chain, src) => {
            return chain.then(() => {
                if (!isLatestNavigationToken(token)) return;
                return loadScriptOnce(src);
            });
        }, Promise.resolve());
    }

    function applyThemePreference() {
        const prefersDark = !!(window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches);
        document.documentElement.classList.toggle("theme-dark", prefersDark);
        const themeLink = document.getElementById("hljs-theme");
        if (!themeLink) return;
        const nextHref = prefersDark ? themeLink.dataset.dark : themeLink.dataset.light;
        if (nextHref && themeLink.getAttribute("href") !== nextHref) {
            themeLink.setAttribute("href", nextHref);
        }
    }

    function startThemePreferenceWatcher() {
        if (themeBound) return;
        themeBound = true;
        applyThemePreference();
        const themeQuery = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
        if (!themeQuery) return;
        if (typeof themeQuery.addEventListener === "function") {
            themeQuery.addEventListener("change", applyThemePreference);
        } else if (typeof themeQuery.addListener === "function") {
            themeQuery.addListener(applyThemePreference);
        }
    }

    function closeNav() {
        const navToggle = document.getElementById("nav-toggle");
        const sidebar = document.getElementById("side-nav");
        const backdrop = document.getElementById("nav-backdrop");
        if (!sidebar || !backdrop || !navToggle) return;
        sidebar.classList.remove("open");
        backdrop.classList.remove("open");
        navToggle.classList.remove("nav-open");
        navToggle.setAttribute("aria-expanded", "false");
    }

    function toggleNav() {
        const navToggle = document.getElementById("nav-toggle");
        const sidebar = document.getElementById("side-nav");
        const backdrop = document.getElementById("nav-backdrop");
        if (!sidebar || !backdrop || !navToggle) return;
        const nextOpen = !sidebar.classList.contains("open");
        sidebar.classList.toggle("open", nextOpen);
        backdrop.classList.toggle("open", nextOpen);
        navToggle.classList.toggle("nav-open", nextOpen);
        navToggle.setAttribute("aria-expanded", nextOpen ? "true" : "false");
    }

    function startSidebarNav() {
        if (navBound) return;
        const navToggle = document.getElementById("nav-toggle");
        const sidebar = document.getElementById("side-nav");
        const backdrop = document.getElementById("nav-backdrop");
        if (!navToggle || !sidebar || !backdrop) return;
        navBound = true;
        navToggle.addEventListener("click", toggleNav);
        backdrop.addEventListener("click", closeNav);
        window.addEventListener("resize", () => {
            if (window.innerWidth > 1100) closeNav();
        });
    }

    function bindPanelSettingsPasswordModal() {
        const modal = document.getElementById("panel-settings-password-modal");
        const cancelBtn = document.getElementById("panel-settings-password-cancel");
        const submitBtn = document.getElementById("panel-settings-password-submit");
        const input = document.getElementById("panel-settings-password-input");
        if (!modal) return;
        if (cancelBtn) {
            cancelBtn.addEventListener("click", () => closePanelSettingsPasswordModal());
        }
        if (submitBtn) {
            submitBtn.addEventListener("click", () => {
                submitPanelSettingsPassword();
            });
        }
        if (input) {
            input.addEventListener("keydown", (event) => {
                if (event.key === "Enter") {
                    event.preventDefault();
                    submitPanelSettingsPassword();
                }
            });
        }
        modal.addEventListener("click", (event) => {
            if (event.target === modal) closePanelSettingsPasswordModal();
        });
    }

    function getPersistentClientId(storageKey = "mcweb.restorePaneClientId") {
        try {
            const existing = String(window.localStorage.getItem(storageKey) || "").trim();
            if (existing) return existing;
        } catch (_) {
            // Ignore storage errors.
        }
        const generated = (window.crypto && typeof window.crypto.randomUUID === "function")
            ? window.crypto.randomUUID()
            : `rp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
        try {
            window.localStorage.setItem(storageKey, generated);
        } catch (_) {
            // Ignore storage errors.
        }
        return generated;
    }

    function clearNavAttentionClasses(node) {
        if (!node) return;
        node.classList.remove("nav-attention", "nav-attention-red", "nav-attention-yellow", "nav-attention-green");
    }

    function normalizeHomeAttention(value) {
        const level = String(value || "").trim().toLowerCase();
        return (level === "red" || level === "yellow" || level === "green") ? level : "none";
    }

    function applyHomeAttention(level) {
        const homeLink = document.getElementById("nav-home-link");
        clearNavAttentionClasses(homeLink);
        const isHomePage = !!homeLink?.classList.contains("active");
        if (level === "red") {
            homeLink?.classList.add("nav-attention-red");
        } else if (level === "yellow") {
            homeLink?.classList.add("nav-attention-yellow");
        } else if (level === "green" && !isHomePage) {
            homeLink?.classList.add("nav-attention-green");
        }
    }

    function applyBackupsAttention(active) {
        const backupsLink = document.getElementById("nav-backups-link");
        clearNavAttentionClasses(backupsLink);
        if (active) backupsLink?.classList.add("nav-attention");
    }

    function applyMaintenanceAttention(active) {
        const maintenanceLink = document.getElementById("nav-maintenance-link");
        clearNavAttentionClasses(maintenanceLink);
        if (active && !maintenanceLink?.classList.contains("active")) {
            maintenanceLink?.classList.add("nav-attention");
        }
    }

    function applyMobileToggleAttention(homeLevel, restoreAttention, cleanupAttention) {
        const navToggle = document.getElementById("nav-toggle");
        clearNavAttentionClasses(navToggle);
        if (!window.matchMedia("(max-width: 1100px)").matches) return;
        if (homeLevel === "red" || restoreAttention || cleanupAttention) {
            navToggle?.classList.add("nav-attention-red");
            return;
        }
        if (homeLevel === "yellow") {
            navToggle?.classList.add("nav-attention-yellow");
            return;
        }
        if (homeLevel === "green") {
            navToggle?.classList.add("nav-attention-green");
        }
    }

    function applyNavAttentionPayload(payload) {
        const navAttention = payload && typeof payload === "object"
            ? (payload.nav_attention && typeof payload.nav_attention === "object" ? payload.nav_attention : payload)
            : {};
        const restoreAttention = !!navAttention.restore_pane_attention && !navAttention.restore_pane_opened_by_self;
        const homeAttention = normalizeHomeAttention(navAttention.home_attention);
        const cleanupAttention = !!navAttention.cleanup_has_missed
            || Number(navAttention.cleanup_missed_runs || 0) > 0;
        applyHomeAttention(homeAttention);
        applyBackupsAttention(restoreAttention);
        applyMaintenanceAttention(cleanupAttention);
        applyMobileToggleAttention(homeAttention, restoreAttention, cleanupAttention);
    }

    function ensureGlobalNotificationModal() {
        let modal = document.getElementById("mcweb-global-notification");
        if (modal) return modal;
        modal = document.createElement("div");
        modal.id = "mcweb-global-notification";
        modal.className = "modal-overlay";
        modal.setAttribute("aria-hidden", "true");
        modal.innerHTML = `
            <div class="modal-card" role="dialog" aria-modal="true" aria-labelledby="mcweb-global-notification-title">
                <h3 id="mcweb-global-notification-title" class="modal-title">Notice</h3>
                <p id="mcweb-global-notification-text" class="modal-text"></p>
                <div id="mcweb-global-notification-actions" class="modal-actions"></div>
            </div>
        `;
        document.body.appendChild(modal);
        return modal;
    }

    function closeGlobalNotification(modal) {
        if (!modal) return;
        modal.classList.remove("open");
        modal.setAttribute("aria-hidden", "true");
    }

    function resolveCsrfToken() {
        const shellConfig = window.__MCWEB_SHELL_CONFIG || {};
        const homeConfig = window.__MCWEB_HOME_CONFIG || {};
        const filesConfig = window.__MCWEB_FILES_CONFIG || {};
        if (shellConfig.csrfToken) return String(shellConfig.csrfToken || "").trim();
        if (homeConfig.csrfToken) return String(homeConfig.csrfToken || "").trim();
        if (filesConfig.csrfToken) return String(filesConfig.csrfToken || "").trim();
        const input = document.querySelector('input[name="csrf_token"]');
        if (input && input.value) return String(input.value || "").trim();
        const maintenanceInput = document.getElementById("maintenance-csrf-token");
        if (maintenanceInput && maintenanceInput.value) return String(maintenanceInput.value || "").trim();
        return "";
    }

    const PANEL_SETTINGS_RELOAD_ACCESS_KEY = "mcweb.panelSettingsReloadAccess";
    let panelSettingsAccessCallback = null;
    let panelSettingsAccessPendingHref = "";
    let panelSettingsAccessGranted = false;

    function _panelSettingsStorage() {
        try {
            return window.sessionStorage;
        } catch (_) {
            return null;
        }
    }

    function getNavigationType() {
        try {
            const entries = typeof window.performance?.getEntriesByType === "function"
                ? window.performance.getEntriesByType("navigation")
                : [];
            if (Array.isArray(entries) && entries[0] && typeof entries[0].type === "string") {
                return entries[0].type;
            }
        } catch (_) {
            // Ignore Performance API lookup failures.
        }
        try {
            if (window.performance?.navigation?.type === 1) {
                return "reload";
            }
        } catch (_) {
            // Ignore legacy Performance API lookup failures.
        }
        return "navigate";
    }

    function consumePanelSettingsReloadGrant() {
        const storage = _panelSettingsStorage();
        if (!storage) return false;
        const raw = String(storage.getItem(PANEL_SETTINGS_RELOAD_ACCESS_KEY) || "").trim();
        storage.removeItem(PANEL_SETTINGS_RELOAD_ACCESS_KEY);
        return raw === "1"
            && getNavigationType() === "reload"
            && window.location.pathname === "/panel-settings";
    }

    function isPanelSettingsAccessFresh() {
        return !!panelSettingsAccessGranted;
    }

    function markPanelSettingsAccessGranted() {
        panelSettingsAccessGranted = true;
        const storage = _panelSettingsStorage();
        if (!storage) return;
        storage.removeItem(PANEL_SETTINGS_RELOAD_ACCESS_KEY);
    }

    function clearPanelSettingsAccess() {
        panelSettingsAccessGranted = false;
        const storage = _panelSettingsStorage();
        if (!storage) return;
        storage.removeItem(PANEL_SETTINGS_RELOAD_ACCESS_KEY);
    }

    function rememberPanelSettingsAccessForReload() {
        const storage = _panelSettingsStorage();
        if (!storage) return;
        if (panelSettingsAccessGranted && currentPath === "/panel-settings") {
            storage.setItem(PANEL_SETTINGS_RELOAD_ACCESS_KEY, "1");
            return;
        }
        storage.removeItem(PANEL_SETTINGS_RELOAD_ACCESS_KEY);
    }

    function closePanelSettingsPasswordModal() {
        const modal = document.getElementById("panel-settings-password-modal");
        const input = document.getElementById("panel-settings-password-input");
        const errorText = document.getElementById("panel-settings-password-error");
        if (!modal) return;
        modal.classList.remove("open");
        modal.setAttribute("aria-hidden", "true");
        if (input) input.value = "";
        if (errorText) {
            errorText.textContent = "";
            errorText.hidden = true;
        }
        panelSettingsAccessCallback = null;
        panelSettingsAccessPendingHref = "";
    }

    function openPanelSettingsPasswordModal({ href, onSuccess } = {}) {
        const modal = document.getElementById("panel-settings-password-modal");
        const input = document.getElementById("panel-settings-password-input");
        const errorText = document.getElementById("panel-settings-password-error");
        if (!modal || !input) return;
        panelSettingsAccessCallback = typeof onSuccess === "function" ? onSuccess : null;
        panelSettingsAccessPendingHref = String(href || "").trim();
        if (errorText) {
            errorText.textContent = "";
            errorText.hidden = true;
        }
        modal.classList.add("open");
        modal.setAttribute("aria-hidden", "false");
        input.value = "";
        input.focus();
    }

    async function submitPanelSettingsPassword() {
        const input = document.getElementById("panel-settings-password-input");
        const errorText = document.getElementById("panel-settings-password-error");
        const password = (input && input.value ? String(input.value) : "").trim();
        if (!password) return;
        const token = resolveCsrfToken();
        try {
            const result = await postJson("/panel-settings/confirm-password", { sudo_password: password }, { csrfToken: token });
            const payload = result.payload || {};
            if (!result.response.ok || payload.ok === false) {
                const message = String(payload.message || "Password incorrect.").trim() || "Password incorrect.";
                if (errorText) {
                    errorText.textContent = message;
                    errorText.hidden = false;
                }
                return;
            }
            markPanelSettingsAccessGranted();
            const callback = panelSettingsAccessCallback;
            const href = panelSettingsAccessPendingHref;
            closePanelSettingsPasswordModal();
            if (typeof callback === "function") {
                callback(password);
            } else if (href) {
                navigateTo(href);
            }
        } catch (_) {
            if (errorText) {
                errorText.textContent = "Failed to verify password. Try again.";
                errorText.hidden = false;
            }
        }
    }

    function requestPanelSettingsAccess(options = {}) {
        const href = String(options.href || "").trim();
        const onSuccess = typeof options.onSuccess === "function" ? options.onSuccess : null;
        const forcePrompt = !!options.forcePrompt;
        if (!forcePrompt && isPanelSettingsAccessFresh()) {
            if (onSuccess) {
                onSuccess();
                return true;
            }
            if (href) {
                navigateTo(href);
                return true;
            }
            return true;
        }
        openPanelSettingsPasswordModal({ href, onSuccess });
        return false;
    }

    async function runBackupFromPrompt(csrfToken) {
        const token = String(csrfToken || resolveCsrfToken() || "").trim();
        if (!token) {
            showGlobalNotification("Unable to run backup yet. Open the Home page and try again.", { title: "Backup" });
            return;
        }
        try {
            const result = await postJson("/backup", {}, { csrfToken: token });
            const payload = result.payload || {};
            if (!result.response.ok || payload.ok === false) {
                const message = String(payload.message || "Backup request failed.").trim();
                showGlobalNotification(message || "Backup request failed.", { title: "Backup" });
                return;
            }
            const successMessage = String(payload.message || "Backup started.").trim();
            showGlobalNotification(successMessage || "Backup started.", { title: "Backup" });
        } catch (_) {
            showGlobalNotification("Network request failed while starting backup.", { title: "Backup" });
        }
    }

    function queuePromptAction(action) {
        if (!action || typeof action !== "object") return;
        const type = String(action.type || action.action || "").trim().toLowerCase();
        if (!type) return;
        pendingPromptAction = { type, requestedAt: Date.now() };
        maybeRunPendingPromptAction();
    }

    async function maybeRunPendingPromptAction() {
        if (!pendingPromptAction || !pendingPromptAction.type) return;
        if (pendingPromptAction.type === "run_backup") {
            const token = resolveCsrfToken();
            if (!token) {
                if (window.location.pathname !== "/") {
                    navigateTo("/");
                }
                return;
            }
            pendingPromptAction = null;
            await runBackupFromPrompt(token);
        }
    }

    function handlePromptAction(action) {
        if (!action || typeof action !== "object") return;
        const type = String(action.action || action.type || "").trim().toLowerCase();
        if (type === "run_backup") {
            queuePromptAction({ type: "run_backup" });
            return;
        }
        if (type === "open_backups") {
            navigateTo("/backups");
            return;
        }
        if (type === "navigate") {
            const href = String(action.href || "").trim();
            if (href) navigateTo(href);
        }
    }

    function renderNotificationActions(modal, actions) {
        const container = modal?.querySelector("#mcweb-global-notification-actions");
        if (!container) return;
        container.innerHTML = "";
        const normalized = Array.isArray(actions) ? actions.filter((item) => item && typeof item === "object") : [];
        const finalActions = normalized.length ? normalized : [{ label: "OK", action: "dismiss", style: "primary" }];
        finalActions.forEach((action, index) => {
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = String(action.label || (action.action === "dismiss" ? "OK" : "OK"));
            const style = String(action.style || "").trim().toLowerCase();
            if (style === "primary") {
                button.className = "btn-backup";
            } else if (style === "secondary") {
                button.className = "btn-secondary";
            } else if (style === "danger") {
                button.className = "btn-stop";
            } else if (finalActions.length === 1 || index === 0) {
                button.className = "btn-backup";
            }
            button.addEventListener("click", () => {
                closeGlobalNotification(modal);
                handlePromptAction(action);
            });
            container.appendChild(button);
        });
    }

    function showGlobalNotification(message, options = {}) {
        const modal = ensureGlobalNotificationModal();
        const text = modal.querySelector("#mcweb-global-notification-text");
        const title = modal.querySelector("#mcweb-global-notification-title");
        if (text) text.textContent = String(message || "Notification");
        if (title && options.title) title.textContent = String(options.title || "Notice");
        renderNotificationActions(modal, options.actions);
        modal.setAttribute("aria-hidden", "false");
        modal.classList.add("open");
    }

    function broadcastMetricsSnapshot(payload) {
        if (!isPrimaryTab) return;
        broadcast.send({ type: "metrics_snapshot", payload });
    }

    function notifyMetricsSubscribers(payload) {
        shellState.metricsSubscribers.forEach((listener) => {
            try {
                listener(payload);
            } catch (_) {
                // Ignore listener failures.
            }
        });
    }

    function dispatchMetricsSnapshot(payload, options = {}) {
        if (!payload || typeof payload !== "object") return;
        const previousStatus = String(shellState.lastServiceStatus || "").trim().toLowerCase();
        const nextStatus = String(payload.service_status || payload.minecraft?.status || "").trim().toLowerCase();
        if (nextStatus) {
            shellState.lastServiceStatus = payload.service_status || payload.minecraft?.status || "";
        }
        shellState.metricsSnapshot = payload;
        window.__MCWEB_LAST_METRICS_SNAPSHOT = payload;
        applyNavAttentionPayload(payload);
        notifyMetricsSubscribers(payload);
        if (previousStatus && nextStatus && previousStatus !== nextStatus) {
            if (previousStatus === "starting" && nextStatus === "running") {
                emitSoundEvent("startup");
            } else if (nextStatus === "shutting down") {
                emitSoundEvent("shutdown");
            } else if (nextStatus === "crashed") {
                emitSoundEvent("error");
            }
        }
        if (!options.fromBroadcast) {
            broadcastMetricsSnapshot(payload);
        }
    }

    function buildPromptActions(payload) {
        if (!payload || typeof payload !== "object") return null;
        const prompt = payload.prompt;
        if (prompt && typeof prompt === "object" && Array.isArray(prompt.actions)) {
            return prompt.actions;
        }
        const code = String(payload.code || "").trim().toLowerCase();
        if (code === "backup_missed") {
            return [
                { label: "Run Backup", action: "run_backup", style: "primary" },
                { label: "Open Backups", action: "open_backups", style: "secondary" },
                { label: "Dismiss", action: "dismiss" },
            ];
        }
        return null;
    }

    function handleNotificationPayload(payload, options = {}) {
        if (!payload || typeof payload !== "object") return;
        const message = String(payload.message || "").trim();
        const actions = buildPromptActions(payload);
        if (message) {
            showGlobalNotification(message, { title: payload.title || "Notice", actions });
        }
        const kind = String(payload.kind || "").trim().toLowerCase();
        if (kind === "error" || kind === "danger") {
            emitSoundEvent("error");
        }
        if (!options.fromBroadcast && isPrimaryTab) {
            broadcast.send({ type: "notification", payload });
        }
    }

    function metricsStreamPath() {
        const clientId = getPersistentClientId("mcweb.clientId");
        return clientId ? `/metrics-stream?client_id=${encodeURIComponent(clientId)}` : "/metrics-stream";
    }

    function stopMetricsStream() {
        if (!metricsEventSource) return;
        try {
            metricsEventSource.close();
        } catch (_) {
            // Ignore close errors.
        }
        metricsEventSource = null;
    }

    function stopNotificationsStream() {
        if (!notificationsEventSource) return;
        try {
            notificationsEventSource.close();
        } catch (_) {
            // Ignore close errors.
        }
        notificationsEventSource = null;
    }

    function startNotificationsStream() {
        if (!isPrimaryTab || notificationsEventSource) return;
        notificationsEventSource = new EventSource("/notifications-stream");
        notificationsEventSource.addEventListener("notification", (event) => {
            try {
                const payload = JSON.parse(event.data || "{}");
                handleNotificationPayload(payload, { fromBroadcast: false });
            } catch (_) {
                // Ignore malformed payloads.
            }
        });
        notificationsEventSource.onerror = () => {
            // EventSource reconnects automatically.
        };
    }

    function startMetricsStream() {
        if (!isPrimaryTab || metricsEventSource) return;
        metricsEventSource = new EventSource(metricsStreamPath());
        metricsEventSource.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data || "{}");
                dispatchMetricsSnapshot(payload);
            } catch (_) {
                // Ignore malformed payloads.
            }
        };
        metricsEventSource.onerror = () => {
            // EventSource reconnects automatically.
        };
    }

    function subscribeMetrics(listener) {
        if (typeof listener !== "function") return () => {};
        shellState.metricsSubscribers.add(listener);
        if (shellState.metricsSnapshot && typeof shellState.metricsSnapshot === "object") {
            try {
                listener(shellState.metricsSnapshot);
            } catch (_) {
                // Ignore initial listener failures.
            }
        }
        return () => shellState.metricsSubscribers.delete(listener);
    }

    async function fetchJson(path) {
        const response = await fetch(path, {
            method: "GET",
            headers: { "X-Requested-With": "XMLHttpRequest", Accept: "application/json" },
            cache: "no-store",
        });
        let payload = null;
        try {
            payload = await response.json();
        } catch (_) {
            payload = null;
        }
        return { response, payload };
    }

    async function fetchText(path, options = {}) {
        const response = await fetch(path, {
            method: "GET",
            headers: {
                "X-Requested-With": "XMLHttpRequest",
                Accept: options.accept || "text/plain, text/markdown;q=0.9, */*;q=0.8",
            },
            cache: options.cache || "no-store",
        });
        const text = await response.text().catch(() => "");
        return { response, text };
    }

    async function postJson(path, body, options = {}) {
        const headers = Object.assign({
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }, options.headers || {});
        if (options.csrfToken) {
            headers["X-CSRF-Token"] = options.csrfToken;
        }
        const response = await fetch(path, {
            method: "POST",
            headers,
            body: JSON.stringify(body || {}),
            cache: "no-store",
        });
        let payload = null;
        try {
            payload = await response.json();
        } catch (_) {
            payload = null;
        }
        return { response, payload };
    }

    function invalidateMaintenanceStateCache(scope) {
        const normalized = String(scope || "").trim().toLowerCase();
        if (!normalized) {
            shellState.maintenanceStateCache.clear();
            return;
        }
        shellState.maintenanceStateCache.delete(normalized);
    }

    function getFreshEntry(entry, ttlMs = CACHE_TTL_MS) {
        if (!entry || typeof entry !== "object") return null;
        const ts = Number(entry.ts || 0);
        if (!Number.isFinite(ts) || ts <= 0) return null;
        if ((Date.now() - ts) > ttlMs) return null;
        return entry;
    }

    async function fetchDeviceNameMap(options = {}) {
        const force = !!options.force;
        const fresh = !force ? getFreshEntry(shellState.deviceMapEntry) : null;
        if (fresh && fresh.payload) {
            return fresh.payload;
        }
        if (!force && shellState.deviceMapPromise) {
            return shellState.deviceMapPromise;
        }
        shellState.deviceMapPromise = fetchJson("/device-name-map")
            .then((result) => {
                if (!result.response.ok) {
                    throw new Error("Failed to load device name map.");
                }
                const nextMap = result.payload && result.payload.map && typeof result.payload.map === "object"
                    ? result.payload.map
                    : {};
                shellState.deviceMapEntry = { ts: Date.now(), payload: nextMap };
                return nextMap;
            })
            .finally(() => {
                shellState.deviceMapPromise = null;
            });
        return shellState.deviceMapPromise;
    }

    function getDeviceNameMapSnapshot() {
        const fresh = getFreshEntry(shellState.deviceMapEntry);
        return fresh && fresh.payload && typeof fresh.payload === "object" ? fresh.payload : {};
    }

    async function fetchConfiguredReadme(options = {}) {
        const force = !!options.force;
        const freshUrlEntry = !force ? getFreshEntry(shellState.readmeUrlEntry) : null;
        let resolvedUrl = freshUrlEntry && typeof freshUrlEntry.payload === "string"
            ? String(freshUrlEntry.payload || "").trim()
            : "";
        if (!resolvedUrl) {
            const resolveUrl = !force && shellState.readmeUrlPromise
                ? shellState.readmeUrlPromise
                : fetchJson("/doc/readme-url")
                    .then((result) => {
                        const configuredUrl = result.response.ok && result.payload && result.payload.url
                            ? String(result.payload.url || "").trim()
                            : "";
                        const url = configuredUrl || README_DEFAULT_PATH;
                        shellState.readmeUrlEntry = { ts: Date.now(), payload: url };
                        return url;
                    })
                    .finally(() => {
                        shellState.readmeUrlPromise = null;
                    });
            shellState.readmeUrlPromise = resolveUrl;
            resolvedUrl = await resolveUrl;
        }
        const freshTextEntry = !force ? getFreshEntry(shellState.readmeTextCache.get(resolvedUrl)) : null;
        if (freshTextEntry && typeof freshTextEntry.payload === "string") {
            return { url: resolvedUrl, text: freshTextEntry.payload };
        }
        if (!force && shellState.readmeTextPromises.has(resolvedUrl)) {
            return shellState.readmeTextPromises.get(resolvedUrl);
        }
        const promise = fetchText(resolvedUrl)
            .then((result) => {
                if (!result.response.ok) {
                    throw new Error(`Network error ${result.response.status}: ${result.response.statusText}`);
                }
                shellState.readmeTextCache.set(resolvedUrl, { ts: Date.now(), payload: result.text });
                return { url: resolvedUrl, text: result.text };
            })
            .finally(() => {
                shellState.readmeTextPromises.delete(resolvedUrl);
            });
        shellState.readmeTextPromises.set(resolvedUrl, promise);
        return promise;
    }

    async function fetchLogText(source, options = {}) {
        const sourceKey = String(source || "").trim().toLowerCase();
        if (!sourceKey) return "";
        const force = !!options.force;
        const fresh = !force ? getFreshEntry(shellState.logTextCache.get(sourceKey)) : null;
        if (fresh && typeof fresh.payload === "string") {
            return fresh.payload;
        }
        if (!force && shellState.logTextPromises.has(sourceKey)) {
            return shellState.logTextPromises.get(sourceKey);
        }
        const promise = fetchJson(`/log-text/${encodeURIComponent(sourceKey)}`)
            .then((result) => {
                if (!result.response.ok || !result.payload) {
                    throw new Error("Failed to load log text.");
                }
                const logs = String(result.payload.logs || "");
                shellState.logTextCache.set(sourceKey, { ts: Date.now(), payload: logs });
                setHomeLogSnapshot(sourceKey, logs);
                return logs;
            })
            .finally(() => {
                shellState.logTextPromises.delete(sourceKey);
            });
        shellState.logTextPromises.set(sourceKey, promise);
        return promise;
    }

    async function fetchFilePageItems(pageKey, path, options = {}) {
        const key = `${String(pageKey || "").trim().toLowerCase()}:${String(path || "").trim()}`;
        const force = !!options.force;
        const fresh = getFreshEntry(shellState.filePageListCache.get(key));
        if (!force && fresh && fresh.payload) {
            return fresh.payload;
        }
        const result = await fetchJson(path);
        if (!result.response.ok || !result.payload || result.payload.ok === false) {
            throw new Error((result.payload && result.payload.message) || "Failed to load file list.");
        }
        shellState.filePageListCache.set(key, { ts: Date.now(), payload: result.payload });
        return result.payload;
    }

    async function fetchLogFileList(source, options = {}) {
        const sourceKey = String(source || "").trim().toLowerCase();
        const force = !!options.force;
        const fresh = getFreshEntry(shellState.logFileListCache.get(sourceKey));
        if (!force && fresh && fresh.payload) {
            return fresh.payload;
        }
        const result = await fetchJson(`/log-files/${encodeURIComponent(sourceKey)}`);
        if (!result.response.ok || !result.payload || result.payload.ok === false) {
            throw new Error((result.payload && result.payload.message) || "Failed to load log file list.");
        }
        shellState.logFileListCache.set(sourceKey, { ts: Date.now(), payload: result.payload });
        return result.payload;
    }

    function invalidateFilePageListCache(pageKey) {
        const normalized = String(pageKey || "").trim().toLowerCase();
        if (!normalized) {
            shellState.filePageListCache.clear();
            shellState.filePageListPromises.clear();
            return;
        }
        Array.from(shellState.filePageListCache.keys()).forEach((key) => {
            if (key.startsWith(`${normalized}:`)) {
                shellState.filePageListCache.delete(key);
            }
        });
        Array.from(shellState.filePageListPromises.keys()).forEach((key) => {
            if (key.startsWith(`${normalized}:`)) {
                shellState.filePageListPromises.delete(key);
            }
        });
    }

    function invalidateLogFileListCache(source) {
        const normalized = String(source || "").trim().toLowerCase();
        if (!normalized) {
            shellState.logFileListCache.clear();
            shellState.logFileListPromises.clear();
            return;
        }
        shellState.logFileListCache.delete(normalized);
        shellState.logFileListPromises.delete(normalized);
    }

    async function fetchViewedFile(path, options = {}) {
        const key = String(path || "").trim();
        if (!key) {
            throw new Error("Failed to load file.");
        }
        const force = !!options.force;
        const fresh = !force ? getFreshEntry(shellState.viewedFileCache.get(key)) : null;
        if (fresh && fresh.payload) {
            return fresh.payload;
        }
        const result = await fetchJson(key);
        if (!result.response.ok || !result.payload || result.payload.ok === false) {
            throw new Error((result.payload && result.payload.message) || "Failed to load file.");
        }
        shellState.viewedFileCache.set(key, { ts: Date.now(), payload: result.payload });
        return result.payload;
    }

    async function fetchMaintenanceState(scope, options = {}) {
        const normalized = String(scope || "backups").trim().toLowerCase() || "backups";
        const force = !!options.force;
        const fresh = !force ? getFreshEntry(shellState.maintenanceStateCache.get(normalized), 30 * 1000) : null;
        if (fresh && fresh.payload) {
            return fresh.payload;
        }
        const path = `/maintenance/api/state?scope=${encodeURIComponent(normalized)}`;
        const result = await fetchJson(path);
        if (!result.response.ok || !result.payload || result.payload.ok === false) {
            const error = result.payload && typeof result.payload === "object" ? result.payload : {};
            throw error;
        }
        shellState.maintenanceStateCache.set(normalized, { ts: Date.now(), payload: result.payload });
        return result.payload;
    }

    async function postMaintenanceJson(path, body, options = {}) {
        const result = await postJson(path, body || {}, {
            csrfToken: options.csrfToken || "",
            headers: Object.assign({ "X-Requested-With": "XMLHttpRequest" }, options.headers || {}),
        });
        if (!result.response.ok || !result.payload || result.payload.ok === false) {
            throw (result.payload || {});
        }
        invalidateMaintenanceStateCache((body && body.scope) || "");
        return result.payload;
    }

    function homeLogLimit(source) {
        return HOME_LOG_LIMITS[source] || 200;
    }

    function capHomeLogLines(lines, maxLines) {
        return Array.isArray(lines) && lines.length > maxLines ? lines.slice(-maxLines) : (Array.isArray(lines) ? lines.slice() : []);
    }

    function notifyHomeLogSubscribers(source) {
        const snapshot = getHomeLogLines(source);
        shellState.homeLogSubscribers.forEach((listener) => {
            try {
                listener(source, snapshot);
            } catch (_) {
                // Ignore listener failures.
            }
        });
    }

    function setHomeLogSnapshot(source, rawText, options = {}) {
        const sourceKey = String(source || "").trim().toLowerCase();
        if (!HOME_LOG_LIMITS[sourceKey]) return;
        const lines = capHomeLogLines(String(rawText || "").split("\n"), homeLogLimit(sourceKey));
        shellState.homeLogs.buffers[sourceKey] = lines;
        notifyHomeLogSubscribers(sourceKey);
        if (!options.fromBroadcast && isPrimaryTab) {
            broadcast.send({ type: "log_snapshot", source: sourceKey, lines });
        }
    }

    function appendHomeLogLine(source, line, options = {}) {
        const sourceKey = String(source || "").trim().toLowerCase();
        if (!HOME_LOG_LIMITS[sourceKey]) return;
        shellState.homeLogs.pending[sourceKey].push(String(line || ""));
        if (shellState.homeLogs.flushTimers[sourceKey]) return;
        shellState.homeLogs.flushTimers[sourceKey] = window.setTimeout(() => {
            shellState.homeLogs.flushTimers[sourceKey] = null;
            const nextLines = shellState.homeLogs.pending[sourceKey].splice(0);
            if (!nextLines.length) return;
            const merged = shellState.homeLogs.buffers[sourceKey].concat(nextLines);
            shellState.homeLogs.buffers[sourceKey] = capHomeLogLines(merged, homeLogLimit(sourceKey));
            notifyHomeLogSubscribers(sourceKey);
        }, 75);
        if (!options.fromBroadcast && isPrimaryTab) {
            broadcast.send({ type: "log_line", source: sourceKey, line: String(line || "") });
        }
    }

    function ensureHomeLogStreamStarted(source) {
        const sourceKey = String(source || "").trim().toLowerCase();
        if (!HOME_LOG_PATHS[sourceKey] || shellState.homeLogs.streams[sourceKey]) return;
        if (!isPrimaryTab) {
            broadcast.send({ type: "request_log_source", source: sourceKey });
            broadcast.send({ type: "request_state" });
            return;
        }
        const clientId = getPersistentClientId("mcweb.clientId");
        const basePath = HOME_LOG_PATHS[sourceKey];
        const streamPath = clientId ? `${basePath}?client_id=${encodeURIComponent(clientId)}` : basePath;
        const stream = new EventSource(streamPath);
        stream.onmessage = (event) => appendHomeLogLine(sourceKey, event.data || "");
        stream.onerror = () => {
            // EventSource reconnects automatically.
        };
        shellState.homeLogs.streams[sourceKey] = stream;
    }

    function stopHomeLogStream(source) {
        const sourceKey = String(source || "").trim().toLowerCase();
        const stream = shellState.homeLogs.streams[sourceKey];
        if (!stream) return;
        try {
            stream.close();
        } catch (_) {
            // Ignore close errors.
        }
        shellState.homeLogs.streams[sourceKey] = null;
    }

    function activateHomeLogStream(source) {
        const sourceKey = String(source || "").trim().toLowerCase();
        Object.keys(shellState.homeLogs.streams).forEach((key) => {
            if (key !== sourceKey) stopHomeLogStream(key);
        });
        shellState.homeLogs.activeSource = sourceKey;
        ensureHomeLogStreamStarted(sourceKey);
    }

    function stopAllHomeLogStreams() {
        Object.keys(shellState.homeLogs.streams).forEach(stopHomeLogStream);
        shellState.homeLogs.activeSource = "";
    }

    function getHomeLogLines(source) {
        const sourceKey = String(source || "").trim().toLowerCase();
        return capHomeLogLines(shellState.homeLogs.buffers[sourceKey], homeLogLimit(sourceKey));
    }

    function refreshAllStates() {
        const activeHomeLog = shellState.homeLogs.activeSource;
        stopMetricsStream();
        stopNotificationsStream();
        stopAllHomeLogStreams();

        shellState.metricsSnapshot = null;
        shellState.deviceMapEntry = null;
        shellState.viewedFileCache.clear();
        shellState.viewedFilePromises.clear();
        shellState.maintenanceStateCache.clear();
        shellState.maintenanceStatePromises.clear();
        shellState.filePageListCache.clear();
        shellState.filePageListPromises.clear();
        shellState.logFileListCache.clear();
        shellState.logFileListPromises.clear();

        Object.keys(HOME_LOG_PATHS).forEach((key) => {
            shellState.homeLogs.buffers[key] = [];
            shellState.homeLogs.pending[key] = [];
            if (shellState.homeLogs.flushTimers[key]) {
                window.clearTimeout(shellState.homeLogs.flushTimers[key]);
                shellState.homeLogs.flushTimers[key] = null;
            }
            setHomeLogSnapshot(key, "", { replace: true });
        });

        startMetricsStream();
        startNotificationsStream();
        if (activeHomeLog) {
            activateHomeLogStream(activeHomeLog);
        }
        navigateTo(window.location.href, { replaceHistory: true });
    }

    function subscribeHomeLogs(listener) {
        if (typeof listener !== "function") return () => {};
        shellState.homeLogSubscribers.add(listener);
        return () => shellState.homeLogSubscribers.delete(listener);
    }


    function getHomeViewState() {
        const state = shellState.homeView;
        return {
            selectedLogSource: state.selectedLogSource,
            logAutoScrollBySource: Object.assign({}, state.logAutoScrollBySource),
            logScrollTopBySource: Object.assign({}, state.logScrollTopBySource),
        };
    }

    function updateHomeViewState(patch = {}) {
        if (!patch || typeof patch !== "object") return getHomeViewState();
        if (typeof patch.selectedLogSource === "string" && patch.selectedLogSource.trim()) {
            shellState.homeView.selectedLogSource = patch.selectedLogSource.trim().toLowerCase();
        }
        if (patch.logAutoScrollBySource && typeof patch.logAutoScrollBySource === "object") {
            Object.assign(shellState.homeView.logAutoScrollBySource, patch.logAutoScrollBySource);
        }
        if (patch.logScrollTopBySource && typeof patch.logScrollTopBySource === "object") {
            Object.assign(shellState.homeView.logScrollTopBySource, patch.logScrollTopBySource);
        }
        persistViewState();
        return getHomeViewState();
    }

    function getFilePageViewState(pageKey) {
        const normalized = String(pageKey || "").trim().toLowerCase();
        const state = shellState.fileViews[normalized] || createDefaultFilePageViewState();
        if (!shellState.fileViews[normalized]) {
            shellState.fileViews[normalized] = state;
        }
        return {
            sortMode: state.sortMode,
            backupSortMode: state.backupSortMode,
            backupFilters: state.backupFilters ? Object.assign({}, state.backupFilters) : null,
            currentLogFileSource: state.currentLogFileSource,
            activeLogSource: state.activeLogSource,
            activeViewedFilename: state.activeViewedFilename,
            activeRestoreFilename: state.activeRestoreFilename,
            restoreLogFilename: state.restoreLogFilename,
            restoreLogLines: Array.isArray(state.restoreLogLines) ? state.restoreLogLines.slice(0) : [],
            restoreLogScrollTop: Number(state.restoreLogScrollTop || 0),
            viewerOpen: !!state.viewerOpen,
            viewerKind: state.viewerKind,
            viewerRequest: state.viewerRequest ? Object.assign({}, state.viewerRequest) : null,
            viewerScrollTop: Number(state.viewerScrollTop || 0),
            listScrollTop: Number(state.listScrollTop || 0),
        };
    }

    function updateFilePageViewState(pageKey, patch = {}) {
        const normalized = String(pageKey || "").trim().toLowerCase();
        const state = shellState.fileViews[normalized] || createDefaultFilePageViewState();
        shellState.fileViews[normalized] = state;
        if (!patch || typeof patch !== "object") return getFilePageViewState(normalized);
        Object.keys(patch).forEach((key) => {
            if (key === "backupFilters") {
                state.backupFilters = patch.backupFilters && typeof patch.backupFilters === "object"
                    ? Object.assign({}, patch.backupFilters)
                    : null;
                return;
            }
            if (key === "viewerRequest") {
                state.viewerRequest = patch.viewerRequest && typeof patch.viewerRequest === "object"
                    ? Object.assign({}, patch.viewerRequest)
                    : null;
                return;
            }
            if (key === "restoreLogLines") {
                state.restoreLogLines = Array.isArray(patch.restoreLogLines)
                    ? patch.restoreLogLines.slice(0, 500)
                    : [];
                return;
            }
            state[key] = patch[key];
        });
        persistViewState();
        return getFilePageViewState(normalized);
    }

    function getMaintenanceViewState() {
        const state = shellState.maintenanceView || createDefaultMaintenanceViewState();
        shellState.maintenanceView = state;
        return {
            currentScope: state.currentScope,
            currentActionView: state.currentActionView,
            historyViewMode: state.historyViewMode,
        };
    }

    function updateMaintenanceViewState(patch = {}) {
        if (!patch || typeof patch !== "object") return getMaintenanceViewState();
        const state = shellState.maintenanceView || createDefaultMaintenanceViewState();
        shellState.maintenanceView = state;
        if (typeof patch.currentScope === "string" && patch.currentScope.trim()) {
            state.currentScope = patch.currentScope.trim().toLowerCase();
        }
        if (typeof patch.currentActionView === "string" && patch.currentActionView.trim()) {
            state.currentActionView = patch.currentActionView.trim().toLowerCase();
        }
        if (typeof patch.historyViewMode === "string" && patch.historyViewMode.trim()) {
            state.historyViewMode = patch.historyViewMode.trim().toLowerCase();
        }
        persistViewState();
        return getMaintenanceViewState();
    }

    function getDocsViewState() {
        const state = shellState.docsView || createDefaultDocsViewState();
        shellState.docsView = state;
        return {
            scrollByUrl: state.scrollByUrl ? Object.assign({}, state.scrollByUrl) : {},
        };
    }

    function updateDocsViewState(patch = {}) {
        if (!patch || typeof patch !== "object") return getDocsViewState();
        const state = shellState.docsView || createDefaultDocsViewState();
        shellState.docsView = state;
        if (patch.scrollByUrl && typeof patch.scrollByUrl === "object") {
            Object.assign(state.scrollByUrl, patch.scrollByUrl);
        }
        persistViewState();
        return getDocsViewState();
    }
    // Public shell API for page runtimes. Shell boot concerns like theme and nav
    // stay internal; pages consume shared caches, live metrics, and view state.
    window.MCWebShell = Object.assign({}, window.MCWebShell || {}, {
        getPersistentClientId,
        subscribeMetrics,
        fetchDeviceNameMap,
        getDeviceNameMapSnapshot,
        fetchConfiguredReadme,
        fetchLogText,
        fetchFilePageItems,
        fetchLogFileList,
        invalidateFilePageListCache,
        invalidateLogFileListCache,
        fetchViewedFile,
        fetchMaintenanceState,
        postMaintenanceJson,
        getHomeLogLines,
        subscribeHomeLogs,
        getHomeViewState,
        updateHomeViewState,
        getFilePageViewState,
        updateFilePageViewState,
        getMaintenanceViewState,
        updateMaintenanceViewState,
        getDocsViewState,
        updateDocsViewState,
        activateHomeLogStream,
        stopAllHomeLogStreams,
        setHomeLogSnapshot,
        refreshAllStates,
        requestPanelSettingsAccess,
        isPanelSettingsAccessFresh,
    });

    async function mountCurrentContent(pathname, title, options = {}) {
        const token = options.navigationToken;
        if (typeof token === "number" && !isLatestNavigationToken(token)) {
            return;
        }
        setActiveNavForPath(pathname);
        closeNav();
        pruneContentRootWhitespace();
        if (!options.skipStyleSync) {
            await syncPageStyles();
            if (typeof token === "number" && !isLatestNavigationToken(token)) {
                return;
            }
        }
        syncPageConfig();
        document.title = title || currentPageManifest()?.getAttribute("data-page-title") || document.title;
        await loadScriptsSequentially(parsePageAssetList("data-page-scripts"), token);
        if (typeof token === "number" && !isLatestNavigationToken(token)) {
            return;
        }
        const pageKey = document.body.dataset.page || "";
        await pageModules.mount(pageKey, {
            pageKey: pageKey,
            pathname: pathname,
            title: document.title,
            shell: window.MCWebShell || null,
        });
        if (typeof token === "number" && !isLatestNavigationToken(token)) {
            return;
        }
        if (typeof window.MCWebEnhanceCustomSelects === "function") {
            window.MCWebEnhanceCustomSelects(contentRoot);
        }
        if (pathname === "/panel-settings" && !panelSettingsAccessGranted) {
            requestPanelSettingsAccess({ forcePrompt: true });
        }
        maybeRunPendingPromptAction();
    }

    function handleNavigationFailure(nextUrl, reason) {
        const offline = window.MCWebOfflineRecovery;
        if (offline && typeof offline.setOfflineIfUnreachable === "function") {
            offline.setOfflineIfUnreachable(reason || "navigation_failed");
        }
    }
    async function navigateTo(url, options = {}) {
        const nextUrl = new URL(url, window.location.href);
        const previousPath = currentPath;
        navigationToken += 1;
        const token = navigationToken;
        if (navigationController) {
            try {
                navigationController.abort();
            } catch (_) {
                // Ignore abort failures for stale navigations.
            }
        }
        navigationController = new AbortController();
        const controller = navigationController;
        const nextPromise = fetch(nextUrl.pathname + nextUrl.search, {
            headers: {
                [FRAGMENT_HEADER]: "1",
                "X-Requested-With": "XMLHttpRequest",
            },
            cache: "no-store",
            signal: controller.signal,
        }).then(async (response) => {
            if (!isLatestNavigationToken(token)) {
                return;
            }
            if (!response.ok) {
                handleNavigationFailure(nextUrl, "navigate_response_error");
                return;
            }
            const rawHtml = await response.text();
            if (!isLatestNavigationToken(token)) {
                return;
            }
            const html = nextUrl.pathname === "/" ? hydrateHomeFragmentHtml(rawHtml) : rawHtml;
            const nextStyles = parseFragmentAssets(html, "data-page-styles");
            await syncPageStyles(nextStyles);
            if (!isLatestNavigationToken(token)) {
                return;
            }
            pageModules.unmount(contentRoot.dataset.currentPage || document.body.dataset.page || "");
            dispatchSyntheticPageHide();
            contentRoot.innerHTML = html;
            pruneContentRootWhitespace();
            await mountCurrentContent(
                nextUrl.pathname,
                response.headers.get("X-MCWEB-Page-Title") || "",
                { skipStyleSync: true, navigationToken: token }
            );
            if (!isLatestNavigationToken(token)) {
                return;
            }
            if (previousPath === "/panel-settings" && nextUrl.pathname !== "/panel-settings") {
                clearPanelSettingsAccess();
            }
            currentPath = nextUrl.pathname;
            const target = nextUrl.pathname + nextUrl.search + nextUrl.hash;
            if (options.replaceHistory) {
                window.history.replaceState({}, "", target);
            } else {
                window.history.pushState({}, "", target);
            }
            if (nextUrl.hash) {
                const anchor = document.getElementById(nextUrl.hash.slice(1));
                if (anchor) anchor.scrollIntoView();
            } else {
                window.scrollTo(0, 0);
            }
        }).catch((error) => {
            if (error && error.name === "AbortError") {
                return;
            }
            handleNavigationFailure(nextUrl, "navigate_fetch_failed");
        }).finally(() => {
            if (navigationController === controller) {
                navigationController = null;
            }
        });
        return nextPromise;
    }

    document.addEventListener("click", (event) => {
        const anchor = event.target instanceof Element ? event.target.closest("a") : null;
        if (!anchor || !isInternalNavLink(anchor)) return;
        const nextUrl = new URL(anchor.href, window.location.href);
        if (nextUrl.pathname === currentPath && !nextUrl.search && !nextUrl.hash) return;
        event.preventDefault();
        if (nextUrl.pathname === "/maintenance" && anchor.classList.contains("nav-attention")) {
            updateMaintenanceViewState({ currentActionView: "history", historyViewMode: "missed" });
        }
        const requiresPassword = String(anchor.getAttribute("data-requires-password") || "") === "1";
        if (requiresPassword) {
            requestPanelSettingsAccess({ href: nextUrl.toString() });
            return;
        }
        navigateTo(nextUrl.toString());
    });

    window.addEventListener("popstate", () => {
        navigateTo(window.location.href, { replaceHistory: true });
    });

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            return;
        }
        startPrimaryElection();
        if (!isPrimaryTab) {
            broadcast.send({ type: "request_state" });
        }
    });

    window.addEventListener("beforeunload", () => {
        rememberPanelSettingsAccessForReload();
        if (isPrimaryTab) {
            try {
                window.localStorage.removeItem(PRIMARY_STORAGE_KEY);
            } catch (_) {
                // Ignore storage failures.
            }
        }
        stopPrimaryStreams();
    });

    startThemePreferenceWatcher();
    startSidebarNav();
    bindPanelSettingsPasswordModal();
    bindSoundUnlock();
    panelSettingsAccessGranted = consumePanelSettingsReloadGrant();
    if (shellState.metricsSnapshot) {
        applyNavAttentionPayload(shellState.metricsSnapshot);
    }
    startPrimaryElection();
    if (!isPrimaryTab) {
        broadcast.send({ type: "request_state" });
    }
    mountCurrentContent(currentPath, document.title, { navigationToken }).catch(() => {});
})();

