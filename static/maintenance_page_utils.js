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

    function normalizeIpToken(value) {
        let text = String(value || "").trim();
        if (!text) return "";
        if (text.includes(",")) {
            text = text.split(",", 1)[0].trim();
        }
        if (text.startsWith("/")) {
            text = text.slice(1).trim();
        }
        if (text.startsWith("[") && text.includes("]")) {
            text = text.slice(1, text.indexOf("]")).trim();
        }
        const zoneIndex = text.indexOf("%");
        if (zoneIndex > 0) {
            text = text.slice(0, zoneIndex).trim();
        }
        if (/^::ffff:/i.test(text)) {
            text = text.slice(7).trim();
        }
        if (/^\d{1,3}(?:\.\d{1,3}){3}:\d+$/.test(text)) {
            text = text.replace(/:\d+$/, "");
        }
        return text;
    }

    function resolveDeviceName(rawValue, deviceMap) {
        const raw = String(rawValue || "").trim();
        const normalized = normalizeIpToken(raw);
        const rawKey = raw.toLowerCase();
        const normalizedKey = normalized.toLowerCase();
        if (rawKey === "mcweb" || normalizedKey === "mcweb") {
            return "system";
        }
        const map = (deviceMap && typeof deviceMap === "object") ? deviceMap : {};
        const candidates = [raw, normalized];
        if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(normalized)) {
            candidates.push(`::ffff:${normalized}`);
        }
        for (let i = 0; i < candidates.length; i += 1) {
            const candidate = String(candidates[i] || "").trim();
            if (!candidate) continue;
            const deviceName = String(map[candidate] || "").trim();
            if (deviceName) {
                return deviceName.toLowerCase() === "mcweb" ? "system" : deviceName;
            }
        }
        return normalized || raw;
    }

    function formatAuditActor(rawActor, deviceMap) {
        const actor = String(rawActor || "").trim();
        if (!actor || actor === "-") return "-";
        return resolveDeviceName(actor, deviceMap);
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
