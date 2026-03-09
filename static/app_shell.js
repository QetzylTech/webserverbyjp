(function () {
    const contentRoot = document.getElementById("mcweb-app-content");
    if (!contentRoot) return;

    const FRAGMENT_HEADER = "X-MCWEB-Fragment";
    const shellPaths = new Set(["/", "/readme", "/backups", "/crash-logs", "/minecraft-logs", "/maintenance"]);
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

    let currentPath = window.location.pathname;
    let navigationToken = 0;
    let navigationController = null;
    let navBound = false;
    let themeBound = false;
    let metricsEventSource = null;
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
            viewerOpen: false,
            viewerKind: "",
            viewerRequest: null,
            viewerScrollTop: 0,
            listScrollTop: 0,
        };
    }

    const shellState = {
        metricsSnapshot: window.__MCWEB_LAST_METRICS_SNAPSHOT || null,
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

            const startBtn = doc.getElementById("start-btn");
            if (startBtn) startBtn.disabled = serviceRunning || lowStorageBlocked;
            const stopBtn = doc.getElementById("stop-btn");
            if (stopBtn) stopBtn.disabled = !serviceRunning;
            const backupBtn = doc.getElementById("backup-btn");
            if (backupBtn) backupBtn.disabled = backupBusy;
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

    function applyMobileToggleAttention(homeLevel, restoreAttention) {
        const navToggle = document.getElementById("nav-toggle");
        clearNavAttentionClasses(navToggle);
        if (!window.matchMedia("(max-width: 1100px)").matches) return;
        if (homeLevel === "red" || restoreAttention) {
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
        applyHomeAttention(homeAttention);
        applyBackupsAttention(restoreAttention);
        applyMobileToggleAttention(homeAttention, restoreAttention);
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

    function dispatchMetricsSnapshot(payload) {
        if (!payload || typeof payload !== "object") return;
        shellState.metricsSnapshot = payload;
        window.__MCWEB_LAST_METRICS_SNAPSHOT = payload;
        applyNavAttentionPayload(payload);
        notifyMetricsSubscribers(payload);
    }

    function metricsStreamPath() {
        const clientId = getPersistentClientId();
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

    function startMetricsStream() {
        if (metricsEventSource) return;
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

    function setHomeLogSnapshot(source, rawText) {
        const sourceKey = String(source || "").trim().toLowerCase();
        if (!HOME_LOG_LIMITS[sourceKey]) return;
        const lines = capHomeLogLines(String(rawText || "").split("\n"), homeLogLimit(sourceKey));
        shellState.homeLogs.buffers[sourceKey] = lines;
        notifyHomeLogSubscribers(sourceKey);
    }

    function appendHomeLogLine(source, line) {
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
    }

    function ensureHomeLogStreamStarted(source) {
        const sourceKey = String(source || "").trim().toLowerCase();
        if (!HOME_LOG_PATHS[sourceKey] || shellState.homeLogs.streams[sourceKey]) return;
        const stream = new EventSource(HOME_LOG_PATHS[sourceKey]);
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
            state[key] = patch[key];
        });
        return getFilePageViewState(normalized);
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
        activateHomeLogStream,
        stopAllHomeLogStreams,
        setHomeLogSnapshot,
    });

    async function mountCurrentContent(pathname, title, options = {}) {
        const token = options.navigationToken;
        if (typeof token === "number" && !isLatestNavigationToken(token)) {
            return;
        }
        setActiveNavForPath(pathname);
        closeNav();
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
    }

    async function navigateTo(url, options = {}) {
        const nextUrl = new URL(url, window.location.href);
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
                window.location.assign(nextUrl.toString());
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
            await mountCurrentContent(
                nextUrl.pathname,
                response.headers.get("X-MCWEB-Page-Title") || "",
                { skipStyleSync: true, navigationToken: token }
            );
            if (!isLatestNavigationToken(token)) {
                return;
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
            window.location.assign(nextUrl.toString());
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
        navigateTo(nextUrl.toString());
    });

    window.addEventListener("popstate", () => {
        navigateTo(window.location.href, { replaceHistory: true });
    });

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            stopMetricsStream();
            stopAllHomeLogStreams();
            return;
        }
        startMetricsStream();
        if (shellState.homeLogs.activeSource) {
            ensureHomeLogStreamStarted(shellState.homeLogs.activeSource);
        }
    });

    window.addEventListener("beforeunload", () => {
        stopMetricsStream();
        stopAllHomeLogStreams();
    });

    startThemePreferenceWatcher();
    startSidebarNav();
    if (shellState.metricsSnapshot) {
        applyNavAttentionPayload(shellState.metricsSnapshot);
    }
    startMetricsStream();
    mountCurrentContent(currentPath, document.title, { navigationToken }).catch(() => {});
})();
