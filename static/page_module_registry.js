(function (global) {
    const registry = new Map();
    let activePageKey = "";
    let activeCleanup = null;

    function normalizeKey(value) {
        return String(value || "").trim().toLowerCase();
    }

    function register(pageKeys, moduleDefinition) {
        const keys = Array.isArray(pageKeys) ? pageKeys : [pageKeys];
        keys.forEach((pageKey) => {
            const normalized = normalizeKey(pageKey);
            if (!normalized) return;
            registry.set(normalized, moduleDefinition || {});
        });
    }

    function resolve(pageKey) {
        return registry.get(normalizeKey(pageKey)) || null;
    }

    function unmount(pageKey) {
        const normalized = normalizeKey(pageKey || activePageKey);
        if (!normalized) return;
        const moduleDefinition = resolve(normalized);
        const cleanup = activePageKey === normalized ? activeCleanup : null;
        if (typeof cleanup === "function") {
            try {
                cleanup();
            } catch (_) {
                // Ignore teardown failures during page transitions.
            }
        } else if (moduleDefinition && typeof moduleDefinition.unmount === "function") {
            try {
                moduleDefinition.unmount();
            } catch (_) {
                // Ignore teardown failures during page transitions.
            }
        }
        if (activePageKey === normalized) {
            activePageKey = "";
            activeCleanup = null;
        }
    }

    function mount(pageKey, context) {
        const normalized = normalizeKey(pageKey);
        const moduleDefinition = resolve(normalized);
        if (!moduleDefinition || typeof moduleDefinition.mount !== "function") return null;
        if (activePageKey) {
            unmount(activePageKey);
        }
        const cleanup = moduleDefinition.mount(context || {});
        activePageKey = normalized;
        activeCleanup = typeof cleanup === "function" ? cleanup : null;
        return activeCleanup;
    }

    global.MCWebPageModules = Object.assign({}, global.MCWebPageModules || {}, {
        register,
        resolve,
        mount,
        unmount,
        getActivePageKey: function () { return activePageKey; },
    });
})(window);
