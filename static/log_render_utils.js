(function (global) {
    function escapeHtml(text) {
        return (text || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/\"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function bracketClass(token) {
        if (/^\[[0-9]{2}:[0-9]{2}:[0-9]{2}\]$/.test(token)) return "log-ts";
        if (/[/]\s*error\]/i.test(token) || /[/]\s*fatal\]/i.test(token)) return "log-level-error";
        if (/[/]\s*warn\]/i.test(token)) return "log-level-warn";
        if (/[/]\s*info\]/i.test(token)) return "log-level-info";
        return "log-bracket";
    }

    function formatTextSegment(text, isLineStart) {
        if (!text) return "";
        if (isLineStart) {
            const match = text.match(/^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})(\s+.*)?$/);
            if (match) {
                const timestamp = `<span class="log-ts">${escapeHtml(match[1])}</span>`;
                const rest = match[2] ? `<span class="log-text">${escapeHtml(match[2])}</span>` : "";
                return timestamp + rest;
            }
        }
        return `<span class="log-text">${escapeHtml(text)}</span>`;
    }

    function formatBracketAwareLogLine(rawLine, options = {}) {
        const raw = String(rawLine || "");
        if (options.highlightErrorLine) {
            const lower = raw.toLowerCase();
            if (lower.includes("error") || lower.includes("overloaded") || lower.includes("delayed")) {
                return `<span class="log-line log-level-error">${escapeHtml(raw)}</span>`;
            }
        }

        const bracketRe = /\[[^\]]*\]/g;
        let out = "";
        let cursor = 0;
        let firstSegment = true;
        let match;
        while ((match = bracketRe.exec(raw)) !== null) {
            const start = match.index;
            const end = start + match[0].length;
            out += formatTextSegment(raw.slice(cursor, start), firstSegment);
            out += `<span class="${bracketClass(match[0])}">${escapeHtml(match[0])}</span>`;
            cursor = end;
            firstSegment = false;
        }
        out += formatTextSegment(raw.slice(cursor), firstSegment);
        return `<span class="log-line">${out || '<span class="log-muted">(empty line)</span>'}</span>`;
    }

    function formatBracketAwareLogHtml(rawText, options = {}) {
        const lines = String(rawText || "").split("\n");
        if (lines.length === 0) {
            return '<span class="log-line"><span class="log-muted">(empty line)</span></span>';
        }
        return lines.map((line) => formatBracketAwareLogLine(line, options)).join("");
    }

    global.MCWebLogUtils = Object.assign({}, global.MCWebLogUtils || {}, {
        escapeHtml,
        formatBracketAwareLogLine,
        formatBracketAwareLogHtml,
        formatLiveLogLine: formatBracketAwareLogLine,
    });
})(window);
