(function (global) {
    const MONTH_ABBREV = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

    function parseCountdown(text) {
        const match = String(text || "").trim().match(/^(\d{1,2}):(\d{2})$/);
        if (!match) return null;
        return (parseInt(match[1], 10) * 60) + parseInt(match[2], 10);
    }

    function parseSessionDuration(text) {
        const match = String(text || "").trim().match(/^(\d{1,2}):(\d{2}):(\d{2})$/);
        if (!match) return null;
        return (parseInt(match[1], 10) * 3600) + (parseInt(match[2], 10) * 60) + parseInt(match[3], 10);
    }

    function formatCountdown(totalSeconds) {
        const safe = Math.max(0, Math.floor(Number(totalSeconds) || 0));
        const minutes = Math.floor(safe / 60);
        const seconds = safe % 60;
        return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }

    function formatSessionDuration(totalSeconds) {
        const safe = Math.max(0, Math.floor(Number(totalSeconds) || 0));
        const hours = Math.floor(safe / 3600);
        const minutes = Math.floor((safe % 3600) / 60);
        const seconds = safe % 60;
        return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
    }

    function parseServerTimeText(text) {
        const match = String(text || "").trim().match(/^([A-Z][a-z]{2})\s+(\d{2}),\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})\s+(AM|PM)(?:\s+(.+))?$/);
        if (!match) return null;
        const monthIndex = MONTH_ABBREV.indexOf(match[1]);
        if (monthIndex < 0) return null;
        const day = parseInt(match[2], 10);
        const year = parseInt(match[3], 10);
        let hour = parseInt(match[4], 10);
        const minute = parseInt(match[5], 10);
        const second = parseInt(match[6], 10);
        const amPm = match[7];
        const zoneLabel = String(match[8] || "").trim();
        if (Number.isNaN(day) || Number.isNaN(year) || Number.isNaN(hour) || Number.isNaN(minute) || Number.isNaN(second)) {
            return null;
        }
        if (hour === 12) {
            hour = amPm === "AM" ? 0 : 12;
        } else if (amPm === "PM") {
            hour += 12;
        }
        return {
            utcMs: Date.UTC(year, monthIndex, day, hour, minute, second),
            zoneLabel,
        };
    }

    function formatServerTimeText(utcMs, zoneLabel) {
        if (utcMs === null || Number.isNaN(utcMs)) return "";
        const date = new Date(utcMs);
        const month = MONTH_ABBREV[date.getUTCMonth()];
        const day = String(date.getUTCDate()).padStart(2, "0");
        const year = String(date.getUTCFullYear());
        let hour24 = date.getUTCHours();
        const minute = String(date.getUTCMinutes()).padStart(2, "0");
        const second = String(date.getUTCSeconds()).padStart(2, "0");
        const amPm = hour24 >= 12 ? "PM" : "AM";
        hour24 = hour24 % 12;
        const hour12 = String(hour24 === 0 ? 12 : hour24).padStart(2, "0");
        const label = String(zoneLabel || "").trim();
        return label
            ? `${month} ${day}, ${year} ${hour12}:${minute}:${second} ${amPm} ${label}`
            : `${month} ${day}, ${year} ${hour12}:${minute}:${second} ${amPm}`;
    }

    global.MCWebHomeTimeUtils = Object.assign({}, global.MCWebHomeTimeUtils || {}, {
        parseCountdown,
        parseSessionDuration,
        formatCountdown,
        formatSessionDuration,
        parseServerTimeText,
        formatServerTimeText,
    });
})(window);
