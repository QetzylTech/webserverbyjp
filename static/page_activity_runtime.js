(function () {
    function createHeartbeatController(config = {}) {
        const path = String(config.path || "").trim();
        const csrfToken = String(config.csrfToken || "");
        const intervalMs = Number(config.intervalMs || 0);
        const clientId = String(config.clientId || "").trim();
        let timerId = null;

        function send() {
            if (!path || document.hidden) return;
            fetch(path, {
                method: "POST",
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                    "X-CSRF-Token": csrfToken,
                    ...(clientId ? { "X-MCWEB-Client-Id": clientId } : {}),
                },
                cache: "no-store",
                keepalive: true,
            }).catch(() => {});
        }

        function stop() {
            if (!timerId) return;
            window.clearInterval(timerId);
            timerId = null;
        }

        function start() {
            if (!path || !Number.isFinite(intervalMs) || intervalMs <= 0 || document.hidden) return;
            send();
            if (timerId) return;
            timerId = window.setInterval(send, intervalMs);
        }

        return {
            send,
            start,
            stop,
        };
    }

    window.MCWebPageActivityRuntime = Object.assign({}, window.MCWebPageActivityRuntime || {}, {
        createHeartbeatController,
    });
})();
