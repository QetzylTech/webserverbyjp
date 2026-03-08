(function () {
    const OFFLINE_BANNER_ID = "mcweb-offline-banner";
    const OFFLINE_BANNER_STATE_KEY = "mcweb.offlineBannerState";
    const RECOVERY_PROBE_INTERVAL_MS = 3000;
    const RECOVERY_RESTORED_BANNER_MS = 1000;
    const OFFLINE_BANNER_GRACE_MS = 15000;
    let offlineActive = false;
    let recoveryTimer = null;
    let recoveryHideTimer = null;
    let recoveryAcknowledging = false;
    let offlineGraceTimer = null;
    let wrappedFetch = false;
    const nativeFetch = (typeof window.fetch === "function") ? window.fetch.bind(window) : null;

    function persistBannerState(state) {
        try {
            if (!state) {
                window.sessionStorage.removeItem(OFFLINE_BANNER_STATE_KEY);
                return;
            }
            window.sessionStorage.setItem(OFFLINE_BANNER_STATE_KEY, String(state));
        } catch (_err) {
            // Ignore storage failures; the banner still works for the current page.
        }
    }

    function readPersistedBannerState() {
        try {
            return String(window.sessionStorage.getItem(OFFLINE_BANNER_STATE_KEY) || "").trim().toLowerCase();
        } catch (_err) {
            return "";
        }
    }

    function clearOfflineGraceTimer() {
        if (!offlineGraceTimer) return;
        window.clearTimeout(offlineGraceTimer);
        offlineGraceTimer = null;
    }

    function ensureBanner() {
        let banner = document.getElementById(OFFLINE_BANNER_ID);
        if (banner) return banner;
        banner = document.createElement("div");
        banner.id = OFFLINE_BANNER_ID;
        banner.className = "mcweb-offline-banner";
        banner.setAttribute("role", "status");
        banner.setAttribute("aria-live", "polite");
        banner.textContent = "Server offline. Waiting for connection and server recovery...";
        document.body.appendChild(banner);
        return banner;
    }

    function showOfflineBanner() {
        const banner = ensureBanner();
        banner.textContent = "Server offline. Waiting for connection and server recovery...";
        banner.classList.remove("restored");
        banner.classList.add("active");
        persistBannerState("offline");
    }

    function hideOfflineBanner() {
        const banner = document.getElementById(OFFLINE_BANNER_ID);
        if (!banner) return;
        banner.classList.remove("restored");
        banner.classList.remove("active");
        persistBannerState("");
    }

    function showRecoveredBanner() {
        const banner = ensureBanner();
        banner.textContent = "Signal restored. Reconnecting...";
        banner.classList.add("restored");
        banner.classList.add("active");
        persistBannerState("restored");
    }

    async function probeServerReachable() {
        if (!nativeFetch) return false;
        try {
            const controller = new AbortController();
            const timeout = window.setTimeout(() => controller.abort(), 3000);
            const response = await nativeFetch("/observed-state?t=" + Date.now(), {
                method: "GET",
                cache: "no-store",
                signal: controller.signal,
                headers: { "Accept": "application/json" },
            });
            window.clearTimeout(timeout);
            return response.ok;
        } catch (_) {
            return false;
        }
    }

    function setOfflineActive(reason) {
        if (offlineActive || offlineGraceTimer) return;
        recoveryAcknowledging = false;
        if (recoveryHideTimer) {
            window.clearTimeout(recoveryHideTimer);
            recoveryHideTimer = null;
        }
        offlineGraceTimer = window.setTimeout(() => {
            offlineGraceTimer = null;
            offlineActive = true;
            showOfflineBanner();
            if (!recoveryTimer) {
                recoveryTimer = window.setInterval(async () => {
                    const ok = await probeServerReachable();
                    if (!ok) return;
                    acknowledgeRecoveryAndReload();
                }, RECOVERY_PROBE_INTERVAL_MS);
            }
        }, OFFLINE_BANNER_GRACE_MS);
        if (reason && window.console) {
            console.warn("[mcweb] offline grace period:", reason);
        }
    }

    async function setOfflineIfUnreachable(reason) {
        if (offlineActive) return;
        const ok = await probeServerReachable();
        if (!ok) {
            setOfflineActive(reason || "server_unreachable");
        } else {
            clearOfflineGraceTimer();
        }
    }

    function acknowledgeRecoveryAndReload() {
        clearOfflineGraceTimer();
        if (recoveryAcknowledging) return;
        recoveryAcknowledging = true;
        if (recoveryTimer) {
            window.clearInterval(recoveryTimer);
            recoveryTimer = null;
        }
        showRecoveredBanner();
        recoveryHideTimer = window.setTimeout(() => {
            recoveryHideTimer = null;
            clearOfflineActive();
            window.location.reload();
        }, RECOVERY_RESTORED_BANNER_MS);
    }

    function clearOfflineActive() {
        clearOfflineGraceTimer();
        if (!offlineActive) return;
        offlineActive = false;
        recoveryAcknowledging = false;
        hideOfflineBanner();
        if (recoveryTimer) {
            window.clearInterval(recoveryTimer);
            recoveryTimer = null;
        }
        if (recoveryHideTimer) {
            window.clearTimeout(recoveryHideTimer);
            recoveryHideTimer = null;
        }
    }

    function installFetchNetworkFailureHook() {
        if (wrappedFetch || !nativeFetch) return;
        window.fetch = async function wrappedMcwebFetch(...args) {
            try {
                return await nativeFetch(...args);
            } catch (err) {
                setOfflineActive("fetch_failed");
                throw err;
            }
        };
        wrappedFetch = true;
    }

    function installEventSourceFailureHook() {
        if (typeof window.EventSource !== "function") return;
        const NativeEventSource = window.EventSource;
        if (NativeEventSource.__mcwebWrapped) return;

        function WrappedEventSource(url, config) {
            const es = new NativeEventSource(url, config);
            es.addEventListener("error", () => {
                setOfflineIfUnreachable("sse_error");
            });
            return es;
        }

        WrappedEventSource.prototype = NativeEventSource.prototype;
        WrappedEventSource.CONNECTING = NativeEventSource.CONNECTING;
        WrappedEventSource.OPEN = NativeEventSource.OPEN;
        WrappedEventSource.CLOSED = NativeEventSource.CLOSED;
        WrappedEventSource.__mcwebWrapped = true;
        window.EventSource = WrappedEventSource;
    }

    function bootOfflineState() {
        const persistedState = readPersistedBannerState();
        if (persistedState === "offline") {
            showOfflineBanner();
            offlineActive = true;
        } else if (persistedState === "restored") {
            showRecoveredBanner();
        }
        if (!navigator.onLine) {
            setOfflineActive("navigator_offline");
            return;
        }
        if (!offlineActive) {
            clearOfflineActive();
        }
    }

    function registerServiceWorker() {
        if (!("serviceWorker" in navigator)) return;
        navigator.serviceWorker.register("/sw.js").catch(() => {});
    }

    window.addEventListener("online", async () => {
        const ok = await probeServerReachable();
        if (ok) {
            if (offlineActive) {
                acknowledgeRecoveryAndReload();
            } else {
                window.location.reload();
            }
        } else {
            setOfflineActive("online_but_server_down");
        }
    });

    window.addEventListener("offline", () => {
        setOfflineActive("navigator_offline_event");
    });

    window.addEventListener("mcweb:stream-error", () => {
        setOfflineIfUnreachable("stream_error_event");
    });

    window.MCWebOfflineRecovery = {
        setOffline: (reason) => setOfflineActive(reason || "external"),
        setOfflineIfUnreachable: (reason) => setOfflineIfUnreachable(reason || "external_probe"),
        clearOffline: () => clearOfflineActive(),
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => {
            registerServiceWorker();
            installEventSourceFailureHook();
            installFetchNetworkFailureHook();
            bootOfflineState();
        });
    } else {
        registerServiceWorker();
        installEventSourceFailureHook();
        installFetchNetworkFailureHook();
        bootOfflineState();
    }
})();
