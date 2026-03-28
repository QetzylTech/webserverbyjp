(function (global) {
    function createHomeLogController(options) {
        const shell = options && options.shell ? options.shell : null;
        const logUtils = options && options.logUtils ? options.logUtils : {};
        const watchVerticalScrollbarClass = options && typeof options.watchVerticalScrollbarClass === "function"
            ? options.watchVerticalScrollbarClass
            : function () { return function () {}; };

        const LOG_SOURCE_KEYS = ["minecraft", "backup", "mcweb", "mcweb_log"];
        const LOG_SOURCE_BUFFER_LIMITS = {
            minecraft: 500,
            backup: 200,
            mcweb: 200,
            mcweb_log: 200,
        };
        const LOG_SOURCE_STREAM_PATHS = {
            minecraft: "/log-stream/minecraft",
            backup: "/log-stream/backup",
            mcweb: "/log-stream/mcweb",
            mcweb_log: "/log-stream/mcweb_log",
        };
        const LOG_STREAM_BATCH_FLUSH_MS = 75;

        const initialHomeViewState = shell && typeof shell.getHomeViewState === "function"
            ? shell.getHomeViewState()
            : null;
        let selectedLogSource = initialHomeViewState && typeof initialHomeViewState.selectedLogSource === "string"
            ? initialHomeViewState.selectedLogSource
            : "minecraft";
        let logAutoScrollEnabled = !!(initialHomeViewState && initialHomeViewState.logAutoScrollBySource
            ? initialHomeViewState.logAutoScrollBySource[selectedLogSource] !== false
            : true);
        let deviceNameMap = {};
        let logElement = null;
        let logElementCleanup = null;
        const logSourceBuffers = {
            minecraft: [],
            backup: [],
            mcweb: [],
            mcweb_log: [],
        };
        const pendingLogLines = {
            minecraft: [],
            backup: [],
            mcweb: [],
            mcweb_log: [],
        };
        const pendingLogFlushTimers = {
            minecraft: null,
            backup: null,
            mcweb: null,
            mcweb_log: null,
        };
        const logStreams = {
            minecraft: null,
            backup: null,
            mcweb: null,
            mcweb_log: null,
        };
        let shellLogUnsubscribe = null;

        function persistHomeViewState(patch) {
            if (!shell || typeof shell.updateHomeViewState !== "function") return;
            shell.updateHomeViewState(patch || {});
        }

        function targetElement() {
            return logElement || document.getElementById("minecraft-log");
        }

        function isLogNearBottom(target, thresholdPx) {
            const el = target || targetElement();
            if (!el) return true;
            const distance = el.scrollHeight - el.clientHeight - el.scrollTop;
            return distance <= (thresholdPx || 24);
        }

        function scrollLogToBottom() {
            const target = targetElement();
            if (!target) return;
            target.scrollTop = target.scrollHeight;
        }

        function restoreActiveLogScroll(target) {
            const el = target || targetElement();
            if (!el || logAutoScrollEnabled) return;
            const state = shell && typeof shell.getHomeViewState === "function" ? shell.getHomeViewState() : null;
            const scrollMap = state && state.logScrollTopBySource ? state.logScrollTopBySource : null;
            const nextTop = scrollMap ? Number(scrollMap[selectedLogSource] || 0) : 0;
            if (Number.isFinite(nextTop) && nextTop >= 0) {
                el.scrollTop = nextTop;
            }
        }

        function capTail(lines, maxLines) {
            if (!Array.isArray(lines)) return [];
            return lines.length > maxLines ? lines.slice(-maxLines) : lines;
        }

        function sourceBufferLimit(source) {
            return LOG_SOURCE_BUFFER_LIMITS[source] || 200;
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

        function buildDeviceNameLookup(map) {
            const lookup = {};
            const source = map && typeof map === "object" ? map : {};
            Object.keys(source).forEach(function (key) {
                const name = String(source[key] || "").trim();
                if (!name) return;
                const rawKey = String(key || "").trim();
                const normalizedKey = normalizeIpToken(rawKey);
                if (rawKey && !lookup[rawKey]) {
                    lookup[rawKey] = name;
                }
                if (normalizedKey && !lookup[normalizedKey]) {
                    lookup[normalizedKey] = name;
                }
                if (/^\d{1,3}(?:\.\d{1,3}){3}$/.test(normalizedKey)) {
                    const mappedIpv6 = `::ffff:${normalizedKey}`;
                    if (!lookup[mappedIpv6]) {
                        lookup[mappedIpv6] = name;
                    }
                }
            });
            return lookup;
        }

        let deviceNameLookup = {};

        function ipReplacement(ipText) {
            const rawIp = String(ipText || "").trim();
            const ip = normalizeIpToken(rawIp);
            if (!ip) return "";
            const mapped = deviceNameLookup[rawIp] || deviceNameLookup[ip];
            return mapped && mapped.trim() ? mapped.trim() : ip;
        }

        function replaceIpsWithDeviceNames(text) {
            const raw = String(text || "");
            const withIpv4 = raw.replace(/\b(?:\d{1,3}\.){3}\d{1,3}\b/g, function (ip) {
                return ipReplacement(ip);
            });
            return withIpv4.replace(/\b(?:[A-Fa-f0-9]{0,4}:){3,7}[A-Fa-f0-9]{0,4}\b/g, function (ip) {
                return ipReplacement(ip);
            });
        }

        function formatMinecraftLogLine(line) {
            return logUtils.formatLiveLogLine(replaceIpsWithDeviceNames(line || ""), { highlightErrorLine: true });
        }

        function formatNonMinecraftLogLine(line) {
            return logUtils.formatLiveLogLine(replaceIpsWithDeviceNames(line || ""));
        }

        function buildLogEntry(source, line) {
            const raw = line || "";
            const formatter = source === "minecraft" ? formatMinecraftLogLine : formatNonMinecraftLogLine;
            return { raw: raw, html: formatter(raw) };
        }

        function setSourceLogText(source, rawText) {
            const normalized = String(rawText || "");
            const lines = normalized ? capTail(normalized.split("\n"), sourceBufferLimit(source)) : [];
            logSourceBuffers[source] = lines.map(function (line) {
                return buildLogEntry(source, line);
            });
        }

        function appendRenderedEntriesToActiveLog(entries, meta) {
            const target = targetElement();
            if (!target) return;
            const wasNearBottom = isLogNearBottom(target);
            if ((meta.previousLength || 0) === 0) {
                target.innerHTML = "";
            }
            const htmlChunk = entries.map(function (entry) {
                return entry.html;
            }).join("");
            if (htmlChunk) {
                target.insertAdjacentHTML("beforeend", htmlChunk);
            }
            const droppedCount = Number(meta.droppedCount || 0);
            for (let i = 0; i < droppedCount; i += 1) {
                if (!target.firstElementChild) break;
                target.removeChild(target.firstElementChild);
            }
            if ((meta.currentLength || 0) === 0) {
                target.innerHTML = formatNonMinecraftLogLine("(no logs)");
            }
            if (logAutoScrollEnabled && wasNearBottom) {
                scrollLogToBottom();
                persistHomeViewState({ logScrollTopBySource: { [selectedLogSource]: target.scrollTop } });
            }
        }

        function flushPendingLogLines(source) {
            const pending = pendingLogLines[source];
            if (!pending || pending.length === 0) return;
            pendingLogLines[source] = [];
            const nextEntries = pending.map(function (line) {
                return buildLogEntry(source, line);
            });
            const targetBuffer = logSourceBuffers[source];
            const previousLength = targetBuffer.length;
            targetBuffer.push.apply(targetBuffer, nextEntries);
            const limit = sourceBufferLimit(source);
            const overflow = Math.max(0, targetBuffer.length - limit);
            if (overflow > 0) {
                targetBuffer.splice(0, overflow);
            }
            if (selectedLogSource !== source) return;
            appendRenderedEntriesToActiveLog(nextEntries, {
                previousLength: previousLength,
                droppedCount: overflow,
                currentLength: targetBuffer.length,
            });
        }

        function appendSourceLogLine(source, line) {
            if (!LOG_SOURCE_KEYS.includes(source)) return;
            pendingLogLines[source].push(line || "");
            if (pendingLogFlushTimers[source]) return;
            pendingLogFlushTimers[source] = window.setTimeout(function () {
                pendingLogFlushTimers[source] = null;
                flushPendingLogLines(source);
            }, LOG_STREAM_BATCH_FLUSH_MS);
        }

        function renderActiveLog() {
            const target = targetElement();
            if (!target) return;
            const wasNearBottom = isLogNearBottom(target);
            const entries = logSourceBuffers[selectedLogSource] || [];
            if (entries.length === 0) {
                target.innerHTML = formatNonMinecraftLogLine("(no logs)");
            } else {
                target.innerHTML = entries.map(function (entry) {
                    return entry.html;
                }).join("");
            }
            if (logAutoScrollEnabled && wasNearBottom) {
                scrollLogToBottom();
            } else {
                restoreActiveLogScroll(target);
            }
            persistHomeViewState({
                selectedLogSource: selectedLogSource,
                logAutoScrollBySource: { [selectedLogSource]: logAutoScrollEnabled },
                logScrollTopBySource: { [selectedLogSource]: target.scrollTop },
            });
        }

        function syncLogBufferFromShell(source, lines) {
            if (!LOG_SOURCE_KEYS.includes(source)) return;
            const rawText = Array.isArray(lines) ? lines.join("\n") : String(lines || "");
            setSourceLogText(source, rawText);
            if (selectedLogSource === source) {
                renderActiveLog();
            }
        }

        function closeLogStream(source) {
            const stream = logStreams[source];
            if (!stream) return;
            try {
                stream.close();
            } catch (_) {
                // Ignore close errors.
            }
            logStreams[source] = null;
        }

        function ensureLogStreamStarted(source) {
            if (shell && typeof shell.activateHomeLogStream === "function") {
                shell.activateHomeLogStream(source);
                return;
            }
            if (logStreams[source]) return;
            const path = LOG_SOURCE_STREAM_PATHS[source];
            if (!path) return;
            const stream = new EventSource(path);
            stream.onmessage = function (event) {
                appendSourceLogLine(source, event.data || "");
            };
            stream.onerror = function () {
                // EventSource reconnects automatically.
            };
            logStreams[source] = stream;
        }

        function activateLogStream(source) {
            LOG_SOURCE_KEYS.forEach(function (key) {
                if (key !== source) {
                    closeLogStream(key);
                }
            });
            ensureLogStreamStarted(source);
        }

        function bindShellLogSubscription() {
            if (!shell || typeof shell.subscribeHomeLogs !== "function") return;
            if (shellLogUnsubscribe) return;
            shellLogUnsubscribe = shell.subscribeHomeLogs(function (source, lines) {
                syncLogBufferFromShell(source, lines);
            });
        }

        function rebuildBufferedEntries() {
            LOG_SOURCE_KEYS.forEach(function (source) {
                if ((logSourceBuffers[source] || []).length > 0) {
                    logSourceBuffers[source] = logSourceBuffers[source].map(function (entry) {
                        return buildLogEntry(source, entry.raw);
                    });
                }
            });
        }

        async function loadDeviceNameMap() {
            try {
                if (shell && typeof shell.getDeviceNameMapSnapshot === "function") {
                    const cachedMap = shell.getDeviceNameMapSnapshot();
                    if (cachedMap && typeof cachedMap === "object" && Object.keys(cachedMap).length > 0) {
                        deviceNameMap = cachedMap;
                        deviceNameLookup = buildDeviceNameLookup(deviceNameMap);
                        rebuildBufferedEntries();
                        renderActiveLog();
                        return cachedMap;
                    }
                }
                const nextMap = shell && typeof shell.fetchDeviceNameMap === "function"
                    ? await shell.fetchDeviceNameMap()
                    : await fetch("/device-name-map", { cache: "no-store" })
                        .then(function (response) { return response.ok ? response.json() : null; })
                        .then(function (payload) { return payload && payload.map ? payload.map : {}; });
                deviceNameMap = nextMap && typeof nextMap === "object" ? nextMap : {};
                deviceNameLookup = buildDeviceNameLookup(deviceNameMap);
                rebuildBufferedEntries();
                renderActiveLog();
                return deviceNameMap;
            } catch (_) {
                return deviceNameMap;
            }
        }

        function syncShellLogSource(source, lines) {
            if (!LOG_SOURCE_KEYS.includes(source) || !Array.isArray(lines)) return;
            logSourceBuffers[source] = lines.map(function (line) {
                return buildLogEntry(source, String(line || ""));
            });
            if (selectedLogSource === source) {
                renderActiveLog();
            }
        }

        function setSelectedSource(source) {
            if (!LOG_SOURCE_KEYS.includes(source)) return selectedLogSource;
            selectedLogSource = source;
            logAutoScrollEnabled = !!(shell && typeof shell.getHomeViewState === "function"
                ? shell.getHomeViewState().logAutoScrollBySource[selectedLogSource] !== false
                : true);
            persistHomeViewState({
                selectedLogSource: selectedLogSource,
                logAutoScrollBySource: { [selectedLogSource]: logAutoScrollEnabled },
            });
            return selectedLogSource;
        }

        function bindLogElement(target) {
            if (typeof logElementCleanup === "function") {
                logElementCleanup();
                logElementCleanup = null;
            }
            logElement = target || document.getElementById("minecraft-log");
            if (!logElement) return function () {};
            const handleScroll = function () {
                logAutoScrollEnabled = isLogNearBottom(logElement);
                persistHomeViewState({
                    selectedLogSource: selectedLogSource,
                    logAutoScrollBySource: { [selectedLogSource]: logAutoScrollEnabled },
                    logScrollTopBySource: { [selectedLogSource]: logElement.scrollTop },
                });
            };
            logElement.addEventListener("scroll", handleScroll);
            const scrollbarCleanup = watchVerticalScrollbarClass(logElement);
            logElementCleanup = function () {
                try {
                    logElement.removeEventListener("scroll", handleScroll);
                } catch (_) {
                    // Ignore remove failures.
                }
                if (typeof scrollbarCleanup === "function") {
                    scrollbarCleanup();
                }
                logElement = null;
            };
            return logElementCleanup;
        }

        function sourceHasEntries(source) {
            return !!((logSourceBuffers[source] || []).length);
        }

        function hydrateFromShell() {
            if (!shell || typeof shell.getHomeLogLines !== "function") return;
            LOG_SOURCE_KEYS.forEach(function (source) {
                const lines = shell.getHomeLogLines(source);
                if (Array.isArray(lines) && lines.length > 0) {
                    syncShellLogSource(source, lines);
                }
            });
        }

        function teardown() {
            if (!shell || typeof shell.activateHomeLogStream !== "function") {
                LOG_SOURCE_KEYS.forEach(closeLogStream);
            }
            LOG_SOURCE_KEYS.forEach(function (source) {
                const timerId = pendingLogFlushTimers[source];
                if (timerId) {
                    clearTimeout(timerId);
                    pendingLogFlushTimers[source] = null;
                }
                pendingLogLines[source] = [];
            });
            if (typeof logElementCleanup === "function") {
                logElementCleanup();
                logElementCleanup = null;
            }
            if (typeof shellLogUnsubscribe === "function") {
                shellLogUnsubscribe();
                shellLogUnsubscribe = null;
            }
        }

        bindShellLogSubscription();

        return {
            getSourceKeys: function () { return LOG_SOURCE_KEYS.slice(); },
            getSelectedSource: function () { return selectedLogSource; },
            getAutoScrollEnabled: function () { return logAutoScrollEnabled; },
            setSelectedSource: setSelectedSource,
            setSourceLogText: setSourceLogText,
            sourceHasEntries: sourceHasEntries,
            scrollLogToBottom: scrollLogToBottom,
            renderActiveLog: renderActiveLog,
            activateLogStream: activateLogStream,
            loadDeviceNameMap: loadDeviceNameMap,
            syncShellLogSource: syncShellLogSource,
            bindLogElement: bindLogElement,
            hydrateFromShell: hydrateFromShell,
            teardown: teardown,
        };
    }

    global.MCWebHomeLogRuntime = Object.assign({}, global.MCWebHomeLogRuntime || {}, {
        createHomeLogController: createHomeLogController,
    });
})(window);
