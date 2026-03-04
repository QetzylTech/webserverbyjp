(function () {
    const OFFLINE_BANNER_ID = "mcweb-offline-banner";
    const RECOVERY_PROBE_INTERVAL_MS = 3000;
    let offlineActive = false;
    let recoveryTimer = null;
    let wrappedFetch = false;
    const nativeFetch = (typeof window.fetch === "function") ? window.fetch.bind(window) : null;

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
        banner.classList.add("active");
    }

    function hideOfflineBanner() {
        const banner = document.getElementById(OFFLINE_BANNER_ID);
        if (!banner) return;
        banner.classList.remove("active");
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
        if (offlineActive) return;
        offlineActive = true;
        showOfflineBanner();
        if (!recoveryTimer) {
            recoveryTimer = window.setInterval(async () => {
                const ok = await probeServerReachable();
                if (!ok) return;
                window.location.reload();
            }, RECOVERY_PROBE_INTERVAL_MS);
        }
        if (reason && window.console) {
            console.warn("[mcweb] offline mode:", reason);
        }
    }

    function clearOfflineActive() {
        if (!offlineActive) return;
        offlineActive = false;
        hideOfflineBanner();
        if (recoveryTimer) {
            window.clearInterval(recoveryTimer);
            recoveryTimer = null;
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

    async function bootOfflineState() {
        if (!navigator.onLine) {
            setOfflineActive("navigator_offline");
            return;
        }
        const ok = await probeServerReachable();
        if (ok) {
            clearOfflineActive();
        } else {
            setOfflineActive("server_unreachable");
        }
    }

    function registerServiceWorker() {
        if (!("serviceWorker" in navigator)) return;
        navigator.serviceWorker.register("/sw.js").catch(() => {});
    }

    window.addEventListener("online", async () => {
        const ok = await probeServerReachable();
        if (ok) {
            window.location.reload();
        } else {
            setOfflineActive("online_but_server_down");
        }
    });

    window.addEventListener("offline", () => {
        setOfflineActive("navigator_offline_event");
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => {
            registerServiceWorker();
            installFetchNetworkFailureHook();
            bootOfflineState();
        });
    } else {
        registerServiceWorker();
        installFetchNetworkFailureHook();
        bootOfflineState();
    }
})();
