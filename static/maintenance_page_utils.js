(function (global) {
    const namespace = global.MCWebMaintenancePage || {};

    function parseDataAttr(bootstrap, name, defaultValue) {
        try {
            return JSON.parse(bootstrap?.dataset?.[name] || "");
        } catch (_err) {
            return defaultValue;
        }
    }

    function appendModalListItem(listEl, text) {
        if (!listEl) return;
        const li = document.createElement("li");
        li.textContent = text;
        listEl.appendChild(li);
    }

    function humanBytes(bytes) {
        const n = Number(bytes || 0);
        if (!Number.isFinite(n)) return "0 B";
        if (n < 1024) return `${n} B`;
        if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
        if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
        return `${(n / (1024 * 1024 * 1024)).toFixed(3)} GB`;
    }

    function formatAuditTimestamp(raw) {
        const text = String(raw || "").trim();
        if (!text || text === "-") return "-";
        const parsed = new Date(text);
        if (Number.isNaN(parsed.getTime())) return text;
        return parsed.toLocaleString(undefined, {
            year: "numeric",
            month: "short",
            day: "2-digit",
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
            hour12: true,
            timeZoneName: "short",
        });
    }

    function formatAuditActor(rawActor, deviceMap) {
        const actor = String(rawActor || "").trim();
        if (!actor || actor === "-") return "-";
        const map = (deviceMap && typeof deviceMap === "object") ? deviceMap : {};
        const deviceName = String(map[actor] || "").trim();
        if (deviceName) return deviceName;
        return actor;
    }

    function reasonText(reasons) {
        if (!Array.isArray(reasons) || reasons.length === 0) return "eligible";
        return reasons.join(", ");
    }

    function summarizeByCategory(preview, category) {
        const items = Array.isArray(preview?.items) ? preview.items : [];
        let count = 0;
        let total = 0;
        items.forEach((item) => {
            if (item?.category !== category) return;
            count += 1;
            total += Number(item.size || 0);
        });
        return { count, total };
    }

    function resolveScopeConfig(config, scope) {
        if (!config || typeof config !== "object") return {};
        const scopes = config.scopes;
        if (scopes && typeof scopes === "object") {
            const scoped = scopes[String(scope || "").trim()];
            if (scoped && typeof scoped === "object") {
                return scoped;
            }
        }
        return config;
    }

    function setPressedState(button, isPressed) {
        if (!button) return;
        button.classList.toggle("active", !!isPressed);
        button.setAttribute("aria-pressed", isPressed ? "true" : "false");
    }

    global.MCWebMaintenancePage = Object.assign({}, namespace, {
        utils: {
            parseDataAttr,
            appendModalListItem,
            humanBytes,
            formatAuditTimestamp,
            formatAuditActor,
            reasonText,
            summarizeByCategory,
            resolveScopeConfig,
            setPressedState,
        },
    });
})(window);
