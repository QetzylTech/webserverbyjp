    (function () {
        const darkModeQuery = window.matchMedia("(prefers-color-scheme: dark)");
        function applyThemePreference() {
            document.documentElement.classList.toggle("theme-dark", darkModeQuery.matches);
        }
        applyThemePreference();
        if (darkModeQuery.addEventListener) {
            darkModeQuery.addEventListener("change", applyThemePreference);
        } else if (darkModeQuery.addListener) {
            darkModeQuery.addListener(applyThemePreference);
        }

        const __MCWEB_FILES_CONFIG = window.__MCWEB_FILES_CONFIG || {};
        const csrfToken = __MCWEB_FILES_CONFIG.csrfToken ?? "";
        const FILE_PAGE_HEARTBEAT_INTERVAL_MS = Number(__MCWEB_FILES_CONFIG.heartbeatIntervalMs || 10000);
        function sendFilePageHeartbeat() {
            fetch("/file-page-heartbeat", {
                method: "POST",
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                    "X-CSRF-Token": csrfToken || "",
                },
                cache: "no-store",
                keepalive: true,
            }).catch(() => {});
        }
        sendFilePageHeartbeat();
        window.setInterval(sendFilePageHeartbeat, FILE_PAGE_HEARTBEAT_INTERVAL_MS);

        const toggle = document.getElementById("nav-toggle");
        const sidebar = document.getElementById("side-nav");
        const backdrop = document.getElementById("nav-backdrop");
        if (!toggle || !sidebar || !backdrop) return;

        function closeNav() {
            sidebar.classList.remove("open");
            backdrop.classList.remove("open");
            toggle.classList.remove("nav-open");
            toggle.setAttribute("aria-expanded", "false");
        }

        function toggleNav() {
            const nextOpen = !sidebar.classList.contains("open");
            sidebar.classList.toggle("open", nextOpen);
            backdrop.classList.toggle("open", nextOpen);
            toggle.classList.toggle("nav-open", nextOpen);
            toggle.setAttribute("aria-expanded", nextOpen ? "true" : "false");
        }

        toggle.addEventListener("click", toggleNav);
        backdrop.addEventListener("click", closeNav);
        window.addEventListener("resize", function () {
            if (window.innerWidth > 1100) closeNav();
        });

        const errorBox = document.getElementById("download-error");
        const sortSelect = document.getElementById("file-sort");
        const backupSortSelect = document.getElementById("backup-sort");
        const backupFilterInputs = Array.from(document.querySelectorAll(".backup-filter"));
        const fileList = document.querySelector(".list");
        const listEmptyDynamic = document.getElementById("list-empty-dynamic");
        const passwordModal = document.getElementById("download-password-modal");
        const passwordTitle = document.getElementById("download-password-title");
        const passwordText = passwordModal ? passwordModal.querySelector(".modal-text") : null;
        const passwordInput = document.getElementById("download-password-input");
        const passwordCancel = document.getElementById("download-password-cancel");
        const passwordSubmit = document.getElementById("download-password-submit");
        const messageModal = document.getElementById("message-modal");
        const messageModalText = document.getElementById("message-modal-text");
        const messageModalOk = document.getElementById("message-modal-ok");
        const wrap = document.querySelector(".wrap");
        const fileViewer = document.getElementById("file-viewer");
        const fileViewerResizer = document.getElementById("file-viewer-resizer");
        const fileViewerTitle = document.getElementById("file-viewer-title");
        const fileViewerContent = document.getElementById("file-viewer-content");
        const fileViewerDownload = document.getElementById("file-viewer-download");
        const fileViewerClose = document.getElementById("file-viewer-close");
        const backupRestoreControls = document.getElementById("backup-restore-controls");
        const backupRestorePassword = document.getElementById("backup-restore-password");
        const backupRestoreStart = document.getElementById("backup-restore-start");
        const backupRestoreCancel = document.getElementById("backup-restore-cancel");
        const backupRestoreRollback = document.getElementById("backup-restore-rollback");
        const pageId = document.body.getAttribute("data-page") || "files";
        const viewerWidthStorageKey = `mcweb.viewerWidth.${pageId}`;
        const viewerHeightStorageKey = `mcweb.viewerHeight.${pageId}`;
        const PANE_ANIMATION_DURATION_MS = 220;
        const paneAnimations = window.MCWebPaneAnimations || null;
        let isResizingViewer = false;
        let selectedRestoreFilename = "";
        let pendingAction = null;
        let reloadAfterMessageClose = false;
        let restorePollTimer = null;
        let restorePollJobId = "";
        let restorePollSeq = 0;
        let undoRestoreFilename = "";
        let viewerCloseTimer = null;

        function setDownloadError(text) {
            if (!errorBox) return;
            if (!text) {
                errorBox.textContent = "";
                errorBox.classList.remove("open");
                return;
            }
            errorBox.textContent = text;
            errorBox.classList.add("open");
        }
        function syncVerticalScrollbarClass(target) {
            if (!target) return;
            const hasVerticalScrollbar = target.scrollHeight > target.clientHeight + 1;
            target.classList.toggle("has-vscroll", hasVerticalScrollbar);
        }
        function watchVerticalScrollbarClass(target) {
            if (!target) return;
            syncVerticalScrollbarClass(target);
            target.addEventListener("scroll", () => syncVerticalScrollbarClass(target), { passive: true });
            window.addEventListener("resize", () => syncVerticalScrollbarClass(target));
            if (window.ResizeObserver) {
                const ro = new ResizeObserver(() => syncVerticalScrollbarClass(target));
                ro.observe(target);
            }
            if (window.MutationObserver) {
                const mo = new MutationObserver(() => syncVerticalScrollbarClass(target));
                mo.observe(target, { childList: true, subtree: true, characterData: true });
            }
        }
        watchVerticalScrollbarClass(fileViewerContent);
        watchVerticalScrollbarClass(fileList);

        function closePasswordModal() {
            if (!passwordModal) return;
            passwordModal.classList.remove("open");
            passwordModal.setAttribute("aria-hidden", "true");
            if (passwordInput) passwordInput.value = "";
            if (passwordSubmit) passwordSubmit.textContent = "Continue";
            pendingAction = null;
        }

        function setViewerDownloadMode(action, text, enabled, payload = {}) {
            if (!fileViewerDownload) return;
            fileViewerDownload.textContent = text;
            fileViewerDownload.disabled = !enabled;
            fileViewerDownload.setAttribute("data-action", action || "");
            fileViewerDownload.setAttribute("data-download-url", payload.downloadUrl || "");
            fileViewerDownload.setAttribute("data-filename", payload.filename || "");
        }

        function setBackupRestoreControlsVisible(visible) {
            if (!backupRestoreControls || pageId !== "backups") return;
            backupRestoreControls.hidden = !visible;
        }

        function getRestorePasswordSeed() {
            if (!backupRestorePassword) return "";
            return (backupRestorePassword.value || "").trim();
        }

        function openBackupRestorePane(filename) {
            if (pageId !== "backups") return;
            selectedRestoreFilename = filename || "";
            openViewer();
            setBackupRestoreControlsVisible(true);
            if (fileViewerTitle) {
                fileViewerTitle.textContent = selectedRestoreFilename
                    ? `Restore: ${selectedRestoreFilename}`
                    : "Restore Backup";
            }
            if (fileViewerContent) {
                const message = selectedRestoreFilename
                    ? `Ready to restore ${selectedRestoreFilename}. Press Restore to continue.`
                    : "Select a backup to restore.";
                fileViewerContent.innerHTML = formatViewerLogHtml(message);
            }
        }

        function appendRestoreLine(at, message) {
            if (!fileViewerContent) return;
            const timeHtml = at ? `<span class="log-ts">[${escapeHtml(at)}]</span> ` : "";
            const lineHtml = `<span class="log-line">${timeHtml}<span class="log-text">${escapeHtml(message || "")}</span></span>`;
            fileViewerContent.insertAdjacentHTML("beforeend", lineHtml);
            fileViewerContent.scrollTop = fileViewerContent.scrollHeight;
        }

        function ensureRestoreViewerOpen(title) {
            if (!fileViewer || !fileViewerContent || !fileViewerTitle) return;
            fileViewerTitle.textContent = title || "Restore Progress";
            openViewer();
        }

        function stopRestorePolling() {
            if (!restorePollTimer) return;
            window.clearTimeout(restorePollTimer);
            restorePollTimer = null;
        }

        function scheduleRestorePoll(delayMs) {
            stopRestorePolling();
            restorePollTimer = window.setTimeout(pollRestoreStatus, delayMs);
        }

        function applyRestoreUndoState(statusPayload) {
            if (pageId !== "backups") return;
            const running = !!(statusPayload && statusPayload.running);
            undoRestoreFilename = statusPayload ? (statusPayload.undo_filename || "") : "";
            const canUndo = !running && !!undoRestoreFilename;
            if (backupRestoreRollback) {
                backupRestoreRollback.disabled = !canUndo;
            }
            setViewerDownloadMode("undo_restore", "Undo", canUndo, { filename: undoRestoreFilename });
        }

        async function pollRestoreStatus() {
            if (pageId !== "backups" || !restorePollJobId) return;
            let response;
            try {
                const params = new URLSearchParams();
                params.set("since", String(restorePollSeq));
                params.set("job_id", restorePollJobId);
                response = await fetch(`/restore-status?${params.toString()}`, {
                    method: "GET",
                    headers: { "X-Requested-With": "XMLHttpRequest" },
                    cache: "no-store",
                });
            } catch (_) {
                scheduleRestorePoll(1200);
                return;
            }

            let payload = null;
            try {
                payload = await response.json();
            } catch (_) {
                payload = null;
            }
            if (!response.ok || !payload || payload.ok === false) {
                appendRestoreLine("", "Unable to fetch restore status.");
                scheduleRestorePoll(1500);
                return;
            }

            const events = Array.isArray(payload.events) ? payload.events : [];
            events.forEach((eventItem) => {
                const seqValue = Number(eventItem && eventItem.seq ? eventItem.seq : 0);
                if (seqValue > restorePollSeq) restorePollSeq = seqValue;
                appendRestoreLine(eventItem.at || "", eventItem.message || "");
            });

            applyRestoreUndoState(payload);
            if (payload.running) {
                scheduleRestorePoll(800);
            } else {
                stopRestorePolling();
                if (payload.result && payload.result.ok) {
                    setDownloadError("");
                } else if (payload.result && payload.result.message) {
                    if (payload.result.error === "pre_restore_snapshot_failed") {
                        showMessageModal(payload.result.message || "Failed to create pre-restore snapshot. Restore cancelled.");
                    }
                    setDownloadError(payload.result.message);
                }
            }
        }

        function startRestoreProgressPanel(jobId, title, startMessage) {
            if (pageId !== "backups") return;
            restorePollJobId = jobId || "";
            restorePollSeq = 0;
            ensureRestoreViewerOpen(title || "Restore Progress");
            if (fileViewerContent) fileViewerContent.innerHTML = "";
            applyRestoreUndoState({ running: true, undo_filename: "" });
            appendRestoreLine("", startMessage || "Restore started.");
            if (restorePollJobId) {
                scheduleRestorePoll(200);
            }
        }

        function sortKeyForItem(item, mode) {
            const name = item.getAttribute("data-name") || "";
            const mtime = Number(item.getAttribute("data-mtime") || "0");
            const size = Number(item.getAttribute("data-size") || "0");
            if (mode === "oldest") return [mtime, name];
            if (mode === "size") return [size, name];
            if (mode === "alpha") return [name];
            if (mode === "reverse_alpha") return [name];
            return [mtime, name];
        }

        function sortItems(items, mode) {
            items.sort((a, b) => {
                const ka = sortKeyForItem(a, mode);
                const kb = sortKeyForItem(b, mode);
                if (mode === "alpha" || mode === "reverse_alpha") {
                    const cmp = ka[0].localeCompare(kb[0]);
                    return mode === "reverse_alpha" ? -cmp : cmp;
                }
                if (mode === "oldest") {
                    if (ka[0] !== kb[0]) return ka[0] - kb[0];
                    return ka[1].localeCompare(kb[1]);
                }
                if (mode === "size") {
                    if (ka[0] !== kb[0]) return kb[0] - ka[0];
                    return ka[1].localeCompare(kb[1]);
                }
                if (ka[0] !== kb[0]) return kb[0] - ka[0];
                return ka[1].localeCompare(kb[1]);
            });
            return items;
        }

        function applyFileSort(mode) {
            if (!fileList) return;
            const items = Array.from(fileList.querySelectorAll("li"));
            sortItems(items, mode).forEach((item) => fileList.appendChild(item));
        }

        function backupCategoryFromName(name) {
            const value = String(name || "").toLowerCase();
            if (value.includes("_manual")) return "manual";
            if (value.includes("_auto")) return "auto";
            if (value.includes("_session")) return "session";
            if (value.includes("_pre_restore") || value.includes("_prerestore")) return "prerestore";
            return "others";
        }

        function applyBackupSortAndFilter() {
            if (!fileList) return;
            const selectedSort = backupSortSelect ? (backupSortSelect.value || "newest") : "newest";
            const enabled = new Set(
                backupFilterInputs
                    .filter((input) => input && input.checked)
                    .map((input) => input.value)
            );
            const items = Array.from(fileList.querySelectorAll("li"));
            sortItems(items, selectedSort).forEach((item) => {
                const name = item.getAttribute("data-name") || "";
                const category = backupCategoryFromName(name);
                const visible = enabled.has(category);
                item.style.display = visible ? "" : "none";
                fileList.appendChild(item);
            });

            if (listEmptyDynamic) {
                const visibleCount = items.filter((item) => item.style.display !== "none").length;
                listEmptyDynamic.style.display = visibleCount > 0 ? "none" : "block";
            }
        }

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
                const m = text.match(/^([A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})(\s+.*)?$/);
                if (m) {
                    const ts = `<span class="log-ts">${escapeHtml(m[1])}</span>`;
                    const rest = m[2] ? `<span class="log-text">${escapeHtml(m[2])}</span>` : "";
                    return ts + rest;
                }
            }
            return `<span class="log-text">${escapeHtml(text)}</span>`;
        }

        function formatViewerLogLine(line) {
            const raw = line || "";
            const lower = raw.toLowerCase();
            if (lower.includes("error") || lower.includes("overloaded") || lower.includes("delayed")) {
                return `<span class="log-line log-level-error">${escapeHtml(raw)}</span>`;
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

        function formatViewerLogHtml(rawText) {
            const lines = String(rawText || "").split("\n");
            if (lines.length === 0) {
                return '<span class="log-line"><span class="log-muted">(empty line)</span></span>';
            }
            return lines.map(formatViewerLogLine).join("");
        }

        function closeViewer() {
            if (!wrap || !fileViewer) return;
            if (viewerCloseTimer) {
                window.clearTimeout(viewerCloseTimer);
                viewerCloseTimer = null;
            }
            const finishClose = () => {
                fileViewer.setAttribute("aria-hidden", "true");
                if (pageId === "backups") {
                    setBackupRestoreControlsVisible(false);
                }
                if (fileViewerResizer) {
                    fileViewerResizer.classList.remove("is-dragging");
                }
                clearPaneAnimation(fileViewer);
                clearFloatingPaneStyles(fileViewer);
            };
            if (!wrap.classList.contains("viewer-open")) {
                finishClose();
                return;
            }
            floatPaneForClose(fileViewer);
            wrap.classList.remove("viewer-open");
            playPaneAnimation(fileViewer, "out", { keepClassOnEnd: true });
            viewerCloseTimer = window.setTimeout(finishClose, PANE_ANIMATION_DURATION_MS + 20);
        }

        function openViewer() {
            if (!wrap || !fileViewer) return;
            if (viewerCloseTimer) {
                window.clearTimeout(viewerCloseTimer);
                viewerCloseTimer = null;
            }
            const alreadyOpen = wrap.classList.contains("viewer-open");
            clearFloatingPaneStyles(fileViewer);
            clearPaneAnimation(fileViewer);
            wrap.classList.add("viewer-open");
            fileViewer.setAttribute("aria-hidden", "false");
            if (!alreadyOpen) {
                playPaneAnimation(fileViewer, "in");
            }
        }

        function clearPaneAnimation(target) {
            if (!paneAnimations) return;
            paneAnimations.clearPaneAnimation(target);
        }

        function playPaneAnimation(target, direction, options = {}) {
            if (!paneAnimations) return;
            paneAnimations.playPaneAnimation(target, direction, isStackedViewerLayout(), options);
        }

        function floatPaneForClose(target) {
            if (!paneAnimations) return;
            paneAnimations.floatPaneForClose(target);
        }

        function clearFloatingPaneStyles(target) {
            if (!paneAnimations) return;
            paneAnimations.clearFloatingPaneStyles(target);
        }

        function clampViewerWidth(px) {
            const minPx = 340;
            const maxPx = Math.max(380, Math.floor(window.innerWidth * 0.75));
            return Math.max(minPx, Math.min(maxPx, Math.round(px)));
        }

        function clampViewerHeight(px) {
            const minPx = 220;
            const maxPx = Math.max(280, Math.floor(window.innerHeight * 0.75));
            return Math.max(minPx, Math.min(maxPx, Math.round(px)));
        }

        function applyViewerWidth(px) {
            if (!wrap) return;
            const clamped = clampViewerWidth(px);
            wrap.style.setProperty("--viewer-width", `${clamped}px`);
            try {
                localStorage.setItem(viewerWidthStorageKey, String(clamped));
            } catch (_) {
                // Ignore storage failures.
            }
        }

        function loadViewerWidth() {
            if (!wrap) return;
            let saved = "";
            try {
                saved = localStorage.getItem(viewerWidthStorageKey) || "";
            } catch (_) {
                saved = "";
            }
            const parsed = Number(saved);
            if (Number.isFinite(parsed) && parsed > 0) {
                applyViewerWidth(parsed);
                return;
            }
            applyViewerWidth(Math.floor(window.innerWidth * 0.4));
        }

        function applyViewerHeight(px) {
            if (!wrap) return;
            const clamped = clampViewerHeight(px);
            wrap.style.setProperty("--viewer-height", `${clamped}px`);
            try {
                localStorage.setItem(viewerHeightStorageKey, String(clamped));
            } catch (_) {
                // Ignore storage failures.
            }
        }

        function loadViewerHeight() {
            if (!wrap) return;
            let saved = "";
            try {
                saved = localStorage.getItem(viewerHeightStorageKey) || "";
            } catch (_) {
                saved = "";
            }
            const parsed = Number(saved);
            if (Number.isFinite(parsed) && parsed > 0) {
                applyViewerHeight(parsed);
                return;
            }
            applyViewerHeight(Math.floor(window.innerHeight * 0.42));
        }

        function isStackedViewerLayout() {
            return window.innerWidth <= 1100;
        }

        function updateViewerWidthFromPointer(clientX) {
            if (!wrap) return;
            const viewportWidth = window.innerWidth;
            const desired = viewportWidth - clientX - 12;
            applyViewerWidth(desired);
        }

        function updateViewerHeightFromPointer(clientY) {
            if (!wrap) return;
            const wrapRect = wrap.getBoundingClientRect();
            const desired = wrapRect.bottom - clientY - 6;
            applyViewerHeight(desired);
        }

        function stopViewerResize() {
            if (!isResizingViewer) return;
            isResizingViewer = false;
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
            if (fileViewerResizer) {
                fileViewerResizer.classList.remove("is-dragging");
            }
        }

        function startViewerResize(event) {
            if (!fileViewerResizer || !wrap || !wrap.classList.contains("viewer-open")) return;
            isResizingViewer = true;
            document.body.style.cursor = isStackedViewerLayout() ? "row-resize" : "col-resize";
            document.body.style.userSelect = "none";
            fileViewerResizer.classList.add("is-dragging");
            if (isStackedViewerLayout()) {
                updateViewerHeightFromPointer(event.clientY);
            } else {
                updateViewerWidthFromPointer(event.clientX);
            }
            event.preventDefault();
        }

        function openPasswordModal(actionRequest) {
            if (!passwordModal || !passwordInput) return;
            pendingAction = actionRequest;
            if (passwordTitle) {
                if (actionRequest.kind === "restore") {
                    passwordTitle.textContent = "Confirm Restore";
                } else if (actionRequest.kind === "undo_restore") {
                    passwordTitle.textContent = "Confirm Undo Restore";
                } else {
                    passwordTitle.textContent = "Enter Password";
                }
            }
            if (passwordText) {
                if (actionRequest.kind === "restore") {
                    passwordText.textContent = `Enter sudo password to restore ${actionRequest.filename}. This will create a new world folder and switch level-name.`;
                } else if (actionRequest.kind === "undo_restore") {
                    passwordText.textContent = "Enter sudo password to undo the last restore. This uses the latest pre-restore snapshot.";
                } else {
                    passwordText.textContent = "Enter sudo password to download this backup.";
                }
            }
            if (passwordSubmit) {
                if (actionRequest.kind === "restore") {
                    passwordSubmit.textContent = "Restore";
                } else if (actionRequest.kind === "undo_restore") {
                    passwordSubmit.textContent = "Undo";
                } else {
                    passwordSubmit.textContent = "Continue";
                }
            }
            passwordInput.value = actionRequest.prefillPassword || "";
            passwordModal.classList.add("open");
            passwordModal.setAttribute("aria-hidden", "false");
            passwordInput.focus();
        }

        function showMessageModal(message, options = {}) {
            closePasswordModal();
            if (!messageModal || !messageModalText) return;
            reloadAfterMessageClose = !!options.reloadAfterClose;
            messageModalText.textContent = message || "";
            messageModal.classList.add("open");
            messageModal.setAttribute("aria-hidden", "false");
        }

        function closeMessageModal() {
            if (!messageModal) return;
            messageModal.classList.remove("open");
            messageModal.setAttribute("aria-hidden", "true");
            if (reloadAfterMessageClose) {
                reloadAfterMessageClose = false;
                window.location.reload();
            }
        }

        async function runBackupDownload(downloadRequest, password) {
            const body = new URLSearchParams();
            body.set("csrf_token", csrfToken || "");
            body.set("sudo_password", password);

            let response;
            try {
                response = await fetch(downloadRequest.url, {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRF-Token": csrfToken || "",
                        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    },
                    body: body.toString(),
                });
            } catch (err) {
                setDownloadError("Download failed. Please try again.");
                return;
            }

            if (!response.ok) {
                let message = "Password incorrect. Download cancelled.";
                let errorCode = "";
                try {
                    const payload = await response.json();
                    if (payload && payload.message) message = payload.message;
                    if (payload && payload.error) errorCode = payload.error;
                } catch (_) {
                    // Keep default message on non-JSON responses.
                }
                if (errorCode === "password_incorrect") {
                    showMessageModal(message);
                } else {
                    setDownloadError(message);
                }
                return;
            }

            const blob = await response.blob();
            const fileUrl = URL.createObjectURL(blob);
            const anchor = document.createElement("a");
            anchor.href = fileUrl;
            anchor.download = downloadRequest.filename;
            document.body.appendChild(anchor);
            anchor.click();
            anchor.remove();
            URL.revokeObjectURL(fileUrl);
        }

        async function runBackupRestore(restoreRequest, password) {
            const body = new URLSearchParams();
            body.set("csrf_token", csrfToken || "");
            body.set("sudo_password", password);
            body.set("filename", restoreRequest.filename || "");

            let response;
            try {
                response = await fetch("/restore-backup", {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRF-Token": csrfToken || "",
                        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    },
                    body: body.toString(),
                });
            } catch (err) {
                setDownloadError("Restore failed. Please try again.");
                return;
            }

            let payload = null;
            try {
                payload = await response.json();
            } catch (_) {
                payload = null;
            }

            if (!response.ok) {
                const message = (payload && payload.message) ? payload.message : "Restore failed.";
                const errorCode = (payload && payload.error) ? payload.error : "";
                if (errorCode === "password_incorrect") {
                    showMessageModal(message);
                } else {
                    setDownloadError(message);
                }
                return;
            }

            const jobId = (payload && payload.job_id) ? payload.job_id : "";
            startRestoreProgressPanel(jobId, "Restore Progress", `Restore requested for ${restoreRequest.filename}.`);
        }

        async function runUndoRestore(password) {
            const body = new URLSearchParams();
            body.set("csrf_token", csrfToken || "");
            body.set("sudo_password", password);

            let response;
            try {
                response = await fetch("/undo-restore", {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRF-Token": csrfToken || "",
                        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                    },
                    body: body.toString(),
                });
            } catch (_) {
                setDownloadError("Undo restore failed. Please try again.");
                return;
            }

            let payload = null;
            try {
                payload = await response.json();
            } catch (_) {
                payload = null;
            }
            if (!response.ok) {
                const message = (payload && payload.message) ? payload.message : "Undo restore failed.";
                const errorCode = (payload && payload.error) ? payload.error : "";
                if (errorCode === "password_incorrect") {
                    showMessageModal(message);
                } else {
                    setDownloadError(message);
                }
                return;
            }

            const jobId = (payload && payload.job_id) ? payload.job_id : "";
            startRestoreProgressPanel(jobId, "Undo Restore Progress", "Undo restore requested.");
        }

        async function runFileView(viewRequest) {
            if (!fileViewer || !fileViewerContent || !fileViewerTitle) return;
            fileViewerTitle.textContent = viewRequest.filename || "File Viewer";
            fileViewerContent.textContent = "Loading...";
            setViewerDownloadMode("download_viewed", "Download", false, {
                downloadUrl: viewRequest.downloadUrl || "",
                filename: viewRequest.filename || "",
            });
            openViewer();

            let response;
            try {
                response = await fetch(viewRequest.url, {
                    method: "GET",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    cache: "no-store",
                });
            } catch (_) {
                fileViewerContent.textContent = "Failed to load file.";
                return;
            }

            let payload = null;
            try {
                payload = await response.json();
            } catch (_) {
                payload = null;
            }
            if (!response.ok || !payload || !payload.ok) {
                const message = (payload && payload.message) ? payload.message : "Failed to load file.";
                fileViewerContent.innerHTML = formatViewerLogHtml(message);
                return;
            }
            fileViewerTitle.textContent = payload.filename || viewRequest.filename || "File Viewer";
            fileViewerContent.innerHTML = formatViewerLogHtml(payload.content || "");
            fileViewerContent.scrollTop = 0;
            setViewerDownloadMode("download_viewed", "Download", true, {
                downloadUrl: viewRequest.downloadUrl || "",
                filename: payload.filename || viewRequest.filename || "",
            });
        }

        if (passwordCancel) {
            passwordCancel.addEventListener("click", () => {
                closePasswordModal();
            });
        }
        if (passwordModal) {
            passwordModal.addEventListener("click", (event) => {
                if (event.target === passwordModal) {
                    closePasswordModal();
                }
            });
        }
        if (messageModal) {
            messageModal.addEventListener("click", (event) => {
                if (event.target === messageModal) {
                    closeMessageModal();
                }
            });
        }
        if (messageModalOk) {
            messageModalOk.addEventListener("click", () => {
                closeMessageModal();
            });
        }
        if (passwordSubmit) {
            passwordSubmit.addEventListener("click", async () => {
                if (!passwordInput || !pendingAction) return;
                const password = (passwordInput.value || "").trim();
                if (!password) return;
                const action = pendingAction;
                closePasswordModal();
                if (action.kind === "restore") {
                    await runBackupRestore(action, password);
                    return;
                }
                if (action.kind === "undo_restore") {
                    await runUndoRestore(password);
                    return;
                }
                await runBackupDownload(action, password);
            });
        }
        if (passwordInput) {
            passwordInput.addEventListener("keydown", (event) => {
                if (event.key === "Enter" && passwordSubmit) {
                    event.preventDefault();
                    passwordSubmit.click();
                }
            });
        }
        if (fileViewerClose) {
            fileViewerClose.addEventListener("click", closeViewer);
        }
        if (backupRestoreStart) {
            backupRestoreStart.addEventListener("click", () => {
                if (pageId !== "backups" || !selectedRestoreFilename) return;
                setDownloadError("");
                openPasswordModal({
                    kind: "restore",
                    filename: selectedRestoreFilename,
                    prefillPassword: getRestorePasswordSeed(),
                });
            });
        }
        if (backupRestoreCancel) {
            backupRestoreCancel.addEventListener("click", () => {
                closeViewer();
            });
        }
        if (backupRestoreRollback) {
            backupRestoreRollback.addEventListener("click", () => {
                if (pageId !== "backups" || backupRestoreRollback.disabled) return;
                setDownloadError("");
                openPasswordModal({
                    kind: "undo_restore",
                    prefillPassword: getRestorePasswordSeed(),
                });
            });
        }
        if (fileViewerResizer) {
            fileViewerResizer.addEventListener("pointerdown", startViewerResize);
            window.addEventListener("pointermove", (event) => {
                if (!isResizingViewer) return;
                if (isStackedViewerLayout()) {
                    updateViewerHeightFromPointer(event.clientY);
                } else {
                    updateViewerWidthFromPointer(event.clientX);
                }
            });
            window.addEventListener("pointerup", stopViewerResize);
            window.addEventListener("pointercancel", stopViewerResize);
            window.addEventListener("blur", stopViewerResize);
        }
        window.addEventListener("resize", () => {
            if (isStackedViewerLayout()) {
                const currentHeight = parseFloat((wrap && wrap.style.getPropertyValue("--viewer-height")) || "0");
                if (Number.isFinite(currentHeight) && currentHeight > 0) {
                    applyViewerHeight(currentHeight);
                }
                return;
            }
            const currentWidth = parseFloat((wrap && wrap.style.getPropertyValue("--viewer-width")) || "0");
            if (Number.isFinite(currentWidth) && currentWidth > 0) {
                applyViewerWidth(currentWidth);
            }
        });
        loadViewerWidth();
        loadViewerHeight();
        if (sortSelect) {
            sortSelect.addEventListener("change", () => {
                applyFileSort(sortSelect.value || "newest");
            });
            applyFileSort(sortSelect.value || "newest");
        }
        if (backupSortSelect) {
            backupSortSelect.addEventListener("change", applyBackupSortAndFilter);
            backupFilterInputs.forEach((input) => {
                input.addEventListener("change", applyBackupSortAndFilter);
            });
            applyBackupSortAndFilter();
        }
        if (fileViewerDownload) {
            fileViewerDownload.addEventListener("click", () => {
                const action = fileViewerDownload.getAttribute("data-action") || "";
                if (action === "undo_restore") {
                    if (fileViewerDownload.disabled) return;
                    openPasswordModal({ kind: "undo_restore" });
                    return;
                }
                const url = fileViewerDownload.getAttribute("data-download-url") || "";
                const filename = fileViewerDownload.getAttribute("data-filename") || "";
                if (!url) return;
                const anchor = document.createElement("a");
                anchor.href = url;
                anchor.download = filename;
                document.body.appendChild(anchor);
                anchor.click();
                anchor.remove();
            });
        }

        document.querySelectorAll(".file-download-btn").forEach((btn) => {
            btn.addEventListener("click", async () => {
                setDownloadError("");
                const url = btn.getAttribute("data-download-url") || "";
                const filename = btn.getAttribute("data-filename") || "backup.zip";
                if (!url) return;
                openPasswordModal({ kind: "download", url, filename });
            });
        });

        document.querySelectorAll(".file-restore-btn").forEach((btn) => {
            btn.addEventListener("click", () => {
                setDownloadError("");
                const filename = btn.getAttribute("data-filename") || "";
                if (!filename) return;
                openBackupRestorePane(filename);
            });
        });
        document.querySelectorAll(".file-view-btn").forEach((btn) => {
            btn.addEventListener("click", async () => {
                setDownloadError("");
                const url = btn.getAttribute("data-view-url") || "";
                const downloadUrl = btn.getAttribute("data-download-url") || "";
                const filename = btn.getAttribute("data-filename") || "File Viewer";
                if (!url) return;
                await runFileView({ url, downloadUrl, filename });
            });
        });
    })();
