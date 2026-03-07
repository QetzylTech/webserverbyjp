(function () {
    const homeLink = document.getElementById("nav-home-link");
    const backupsLink = document.getElementById("nav-backups-link");
    const navToggle = document.getElementById("nav-toggle");
    const sidebar = document.getElementById("side-nav");
    const backdrop = document.getElementById("nav-backdrop");
    const mobileQuery = window.matchMedia("(max-width: 1100px)");
    const themeQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const restorePaneClientIdStorageKey = "mcweb.restorePaneClientId";
    let metricsEventSource = null;
    let navBound = false;
    let themeBound = false;

    function applyThemePreference() {
        document.documentElement.classList.toggle("theme-dark", !!themeQuery.matches);
    }

    function startThemePreferenceWatcher() {
        if (themeBound) return;
        themeBound = true;
        applyThemePreference();
        if (typeof themeQuery.addEventListener === "function") {
            themeQuery.addEventListener("change", applyThemePreference);
        } else if (typeof themeQuery.addListener === "function") {
            themeQuery.addListener(applyThemePreference);
        }
    }

    function closeNav() {
        if (!sidebar || !backdrop || !navToggle) return;
        sidebar.classList.remove("open");
        backdrop.classList.remove("open");
        navToggle.classList.remove("nav-open");
        navToggle.setAttribute("aria-expanded", "false");
    }

    function toggleNav() {
        if (!sidebar || !backdrop || !navToggle) return;
        const nextOpen = !sidebar.classList.contains("open");
        sidebar.classList.toggle("open", nextOpen);
        backdrop.classList.toggle("open", nextOpen);
        navToggle.classList.toggle("nav-open", nextOpen);
        navToggle.setAttribute("aria-expanded", nextOpen ? "true" : "false");
    }

    function startSidebarNav() {
        if (navBound || !navToggle || !sidebar || !backdrop) return;
        navBound = true;
        navToggle.addEventListener("click", toggleNav);
        backdrop.addEventListener("click", closeNav);
        window.addEventListener("resize", () => {
            if (window.innerWidth > 1100) closeNav();
        });
    }

    function getPersistentClientId(storageKey = restorePaneClientIdStorageKey) {
        try {
            const existing = String(window.localStorage.getItem(storageKey) || "").trim();
            if (existing) return existing;
        } catch (_) {
            // Ignore storage errors and fall back to a generated id.
        }
        const generated = (window.crypto && typeof window.crypto.randomUUID === "function")
            ? window.crypto.randomUUID()
            : `rp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
        try {
            window.localStorage.setItem(storageKey, generated);
        } catch (_) {
            // Ignore storage errors and keep the in-memory id.
        }
        return generated;
    }

    function clearNavAttentionClasses(node) {
        if (!node) return;
        node.classList.remove("nav-attention", "nav-attention-red", "nav-attention-yellow", "nav-attention-green");
    }

    function applyHomeAttention(level) {
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
        clearNavAttentionClasses(backupsLink);
        if (active) {
            backupsLink?.classList.add("nav-attention");
        }
    }

    function applyMobileToggleAttention(homeLevel, restoreAttention) {
        clearNavAttentionClasses(navToggle);
        if (!mobileQuery.matches) return;
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

    function normalizeHomeAttention(value) {
        const level = String(value || "").trim().toLowerCase();
        return (level === "red" || level === "yellow" || level === "green") ? level : "none";
    }

    function dispatchMetricsSnapshot(payload) {
        if (!payload || typeof payload !== "object") return;
        window.__MCWEB_LAST_METRICS_SNAPSHOT = payload;
        try {
            window.dispatchEvent(new CustomEvent("mcweb:metrics-snapshot", { detail: payload }));
        } catch (_) {
            // Ignore event dispatch errors.
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

    function metricsStreamPath() {
        const clientId = getPersistentClientId();
        return clientId
            ? `/metrics-stream?client_id=${encodeURIComponent(clientId)}`
            : "/metrics-stream";
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

    function startMetricsStream() {
        if (document.hidden || metricsEventSource) return;
        metricsEventSource = new EventSource(metricsStreamPath());
        metricsEventSource.onmessage = (event) => {
            try {
                const payload = JSON.parse(event.data || "{}");
                dispatchMetricsSnapshot(payload);
                applyNavAttentionPayload(payload);
            } catch (_) {
                // Ignore malformed stream payloads.
            }
        };
        metricsEventSource.onerror = () => {
            // EventSource reconnects automatically.
        };
    }

    window.MCWebShell = Object.assign({}, window.MCWebShell || {}, {
        startSidebarNav,
        startThemePreferenceWatcher,
        getPersistentClientId,
    });

    window.MCWebMetricsStream = {
        start: startMetricsStream,
        stop: stopMetricsStream,
        getClientId: () => getPersistentClientId(),
    };

    startThemePreferenceWatcher();
    startSidebarNav();
    window.addEventListener("mcweb:metrics-snapshot", (event) => {
        applyNavAttentionPayload(event.detail || {});
    });
    startMetricsStream();

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            stopMetricsStream();
            return;
        }
        startMetricsStream();
    });
    window.addEventListener("beforeunload", stopMetricsStream);
})();
