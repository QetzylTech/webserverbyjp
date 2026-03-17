(function (global) {
    function createMaintenanceApi(options) {
        const shell = options && options.shell ? options.shell : null;
        const http = options && options.http ? options.http : null;
        const csrfToken = options && options.csrfToken ? String(options.csrfToken) : "";

        async function postJson(path, body) {
            if (shell && typeof shell.postMaintenanceJson === "function") {
                return shell.postMaintenanceJson(path, body || {}, { csrfToken: csrfToken });
            }
            let response;
            let payload;
            if (http) {
                const result = await http.postJson(path, body || {}, {
                    csrfToken: csrfToken,
                    headers: { "X-Requested-With": "XMLHttpRequest" },
                });
                response = result.response;
                payload = result.payload;
            } else {
                response = await fetch(path, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRF-Token": csrfToken,
                    },
                    body: JSON.stringify(body || {}),
                    cache: "no-store",
                });
                payload = await response.json().catch(function () { return {}; });
            }
            if (!response.ok || !payload.ok) {
                throw payload;
            }
            return payload;
        }

        async function fetchState(scope, options) {
            const requestedScope = String(scope || "backups");
            const force = !!(options && options.force);
            if (shell && typeof shell.fetchMaintenanceState === "function") {
                return shell.fetchMaintenanceState(requestedScope, { force: force });
            }
            const refreshParam = force ? "&refresh=1" : "";
            const statePath = `/maintenance/api/state?scope=${encodeURIComponent(requestedScope)}${refreshParam}`;
            let response;
            let payload;
            if (http) {
                const result = await http.getJson(statePath, { headers: { "X-Requested-With": "XMLHttpRequest" } });
                response = result.response;
                payload = result.payload;
            } else {
                response = await fetch(statePath, {
                    headers: {
                        Accept: "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    cache: "no-store",
                });
                payload = await response.json();
            }
            if (!response.ok || !payload.ok) {
                throw (payload || {});
            }
            return payload;
        }

        return {
            postJson: postJson,
            fetchState: fetchState,
        };
    }

    global.MCWebMaintenanceApiRuntime = Object.assign({}, global.MCWebMaintenanceApiRuntime || {}, {
        createMaintenanceApi: createMaintenanceApi,
    });
})(window);
