// File pages keep only transient viewer/restore UI state locally. Shared data
// such as metrics, file lists, and log text comes from the persistent shell.
let teardownFileBrowserPage = null;
const pageModules = window.MCWebPageModules || null;
function mountFileBrowserPage() {
        if (typeof teardownFileBrowserPage === "function") {
            try {
                teardownFileBrowserPage();
            } catch (_) {
                // Ignore stale file-page teardown failures before remounting.
            }
        }
        const __MCWEB_FILES_CONFIG = window.__MCWEB_FILES_CONFIG || {};
        const csrfToken = __MCWEB_FILES_CONFIG.csrfToken ?? "";
        const http = window.MCWebHttp || null;
        const shell = window.MCWebShell || null;
        const domUtils = window.MCWebDomUtils || {};
        const cleanup = typeof domUtils.createCleanupStack === "function"
            ? domUtils.createCleanupStack()
            : null;

        function addScopedListener(target, type, handler, options) {
            if (!target || typeof target.addEventListener !== "function") return;
            target.addEventListener(type, handler, options);
            if (cleanup && typeof cleanup.add === "function") {
                cleanup.add(() => {
                    try {
                        target.removeEventListener(type, handler, options);
                    } catch (_) {
                        // Ignore listener teardown failures.
                    }
                });
            }
        }
        const logUtils = window.MCWebLogUtils || {};
        const viewerRuntime = window.MCWebFileViewerRuntime || {};
        const dataRuntime = window.MCWebFilePageDataRuntime || {};
        const escapeHtml = typeof logUtils.escapeHtml === "function" ? logUtils.escapeHtml : (text) => String(text || "");
        const FILE_PAGE_HEARTBEAT_INTERVAL_MS = Number(__MCWEB_FILES_CONFIG.heartbeatIntervalMs || 10000);
        const pageActivityRuntime = window.MCWebPageActivityRuntime;
        const fileHeartbeatController = pageActivityRuntime.createHeartbeatController({
            path: "/file-page-heartbeat",
            csrfToken,
            intervalMs: FILE_PAGE_HEARTBEAT_INTERVAL_MS,
        });
        fileHeartbeatController.start();


        const errorBox = document.getElementById("download-error");
        const sortSelect = document.getElementById("file-sort");
        const backupSortSelect = document.getElementById("backup-sort");
        const backupFilterInputs = Array.from(document.querySelectorAll(".backup-filter"));
        const logSourceToggles = Array.from(document.querySelectorAll(".log-source-toggle"));
        let fileList = document.querySelector(".list");
        const listEmptyDynamic = document.getElementById("list-empty-dynamic");
        let listLoading = document.getElementById("list-loading");
        const passwordModal = document.getElementById("download-password-modal");
        const passwordTitle = document.getElementById("download-password-title");
        const passwordText = passwordModal ? passwordModal.querySelector(".modal-text") : null;
        const passwordInput = document.getElementById("download-password-input");
        const passwordCancel = document.getElementById("download-password-cancel");
        const passwordSubmit = document.getElementById("download-password-submit");
        const messageModal = document.getElementById("message-modal");
        const messageModalText = document.getElementById("message-modal-text");
        const messageModalOk = document.getElementById("message-modal-ok");
        const successModal = document.getElementById("success-modal");
        const successModalText = document.getElementById("success-modal-text");
        const successModalOk = document.getElementById("success-modal-ok");
        const errorModal = document.getElementById("error-modal");
        const errorModalText = document.getElementById("error-modal-text");
        const errorModalOk = document.getElementById("error-modal-ok");
        const wrap = document.querySelector(".wrap");
        const fileViewer = document.getElementById("file-viewer");
        const fileViewerResizer = document.getElementById("file-viewer-resizer");
        const fileViewerTitle = document.getElementById("pane-title-viewer");
        const fileViewerContent = document.getElementById("file-viewer-content");
        const fileViewerDownload = document.getElementById("file-viewer-download");
        const fileViewerClose = document.getElementById("file-viewer-close");
        const backupRestoreControls = document.getElementById("backup-restore-controls");
        const backupRestoreStart = document.getElementById("backup-restore-start");
        const backupRestoreCancel = document.getElementById("backup-restore-cancel");
        const pageId = document.body.getAttribute("data-page") || "files";
        const listApiPath = String(__MCWEB_FILES_CONFIG.listApiPath || "").trim();
        const emptyText = String(__MCWEB_FILES_CONFIG.emptyText || "No files found.").trim();
        const initialLogFileSource = String(__MCWEB_FILES_CONFIG.initialLogSource || "").trim().toLowerCase();
        const dataClient = (dataRuntime && typeof dataRuntime.createFilePageDataClient === "function")
            ? dataRuntime.createFilePageDataClient({ shell, pageId, listApiPath })
            : null;
        const viewerWidthStorageKey = `mcweb.viewerWidth.${pageId}`;
        const viewerHeightStorageKey = `mcweb.viewerHeight.${pageId}`;
        const FILE_LISTS_INVALIDATED_EVENT = "mcweb:file-lists-invalidated";
        const PANE_ANIMATION_DURATION_MS = 220;
        const paneAnimations = window.MCWebPaneAnimations || null;
        let selectedRestoreFilename = "";
        let selectedRestoreDisplayName = "";
        let pendingAction = null;
        let reloadAfterMessageClose = false;
        let restorePollTimer = null;
        let restorePollJobId = "";
        let restorePollSeq = 0;
        let restoreOperationPollTimer = null;
        let restoreOperationOpId = "";
        let restorePaneAlertTimer = null;
        let restoreServerIsOff = false;
        let activeViewedFilename = "";
        let activeRestoreFilename = "";
        let remoteRestoreActive = false;
        let remoteRestoreFilename = "";
        let remoteRestoreOpenedByName = "";
        let restorePaneForcedByRemote = false;
        let activeLogSource = "";
        let currentLogFileSource = "";
        let fileListClickBound = false;
        let fileMetricsUnsubscribe = null;
        let fileViewerScrollbarCleanup = null;
        let fileListScrollbarCleanup = null;
        let hasRestoredShellViewState = false;
        let pageRuntimeActive = true;
        let fileListLoadToken = 0;

        function nextFileListLoadToken() {
            fileListLoadToken += 1;
            return fileListLoadToken;
        }

        function isCurrentFileListLoadToken(token) {
            return pageRuntimeActive && token === fileListLoadToken;
        }

        const restorePaneClientId = (() => {
            const shell = window.MCWebShell;
            if (shell && typeof shell.getPersistentClientId === "function") {
                return shell.getPersistentClientId("mcweb.restorePaneClientId");
            }
            return (window.crypto && typeof window.crypto.randomUUID === "function")
                ? window.crypto.randomUUID()
                : `rp-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
        })();


        function getPersistedFileViewState() {
            return shell && typeof shell.getFilePageViewState === "function"
                ? shell.getFilePageViewState(pageId)
                : {};
        }

        function persistFileViewState(patch = {}) {
            if (!shell || typeof shell.updateFilePageViewState !== "function") return;
            shell.updateFilePageViewState(pageId, patch);
        }

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
        const watchVerticalScrollbarClass = typeof domUtils.watchVerticalScrollbarClass === "function"
            ? (target) => domUtils.watchVerticalScrollbarClass(target, { observeMutations: true })
            : () => {};
        fileViewerScrollbarCleanup = watchVerticalScrollbarClass(fileViewerContent);
        fileListScrollbarCleanup = watchVerticalScrollbarClass(fileList);
        if (fileViewerContent) {
            addScopedListener(fileViewerContent, "scroll", () => {
                persistFileViewState({ viewerScrollTop: fileViewerContent.scrollTop });
            });
        }
        if (fileList) {
            addScopedListener(fileList, "scroll", () => {
                persistFileViewState({ listScrollTop: fileList.scrollTop });
            });
        }

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

        function normalizeFilename(value) {
            return String(value || "").trim().toLowerCase();
        }

        function applyActiveFileRowHighlight() {
            if (!fileList) return;
            const viewed = normalizeFilename(activeViewedFilename);
            const restoring = normalizeFilename(activeRestoreFilename);
            const remoteName = normalizeFilename(remoteRestoreFilename);
            fileList.querySelectorAll("li").forEach((row) => {
                const rowName = normalizeFilename(row.getAttribute("data-filename") || row.getAttribute("data-name") || "");
                const isViewed = !!viewed && rowName === viewed;
                const isRestoring = !!restoring && rowName === restoring;
                row.classList.toggle("file-row-active", isViewed || isRestoring);
                row.classList.toggle("file-row-active-view", isViewed);
                row.classList.toggle("file-row-active-restore", isRestoring);
                row.classList.toggle("file-row-remote-restore", remoteRestoreActive && !!remoteName && rowName === remoteName);

                const existing = row.querySelector(".restore-open-indicator");
                if (existing) existing.remove();
                if (remoteRestoreActive && !!remoteName && rowName === remoteName) {
                    const indicator = document.createElement("div");
                    indicator.className = "restore-open-indicator";
                    indicator.textContent = `Restore pane is currently open in ${(remoteRestoreOpenedByName || "unknown")}'s browser`;
                    row.prepend(indicator);
                }
            });
        }

        function setActiveViewedFilename(filename) {
            activeViewedFilename = filename || "";
            persistFileViewState({ activeViewedFilename });
            applyActiveFileRowHighlight();
        }

        function setActiveRestoreFilename(filename) {
            activeRestoreFilename = filename || "";
            persistFileViewState({ activeRestoreFilename });
            applyActiveFileRowHighlight();
        }

        function setActiveLogSource(source) {
            activeLogSource = String(source || "").trim().toLowerCase();
            persistFileViewState({ activeLogSource });
            logSourceToggles.forEach((btn) => {
                const sourceKey = String(btn.getAttribute("data-log-source") || "").trim().toLowerCase();
                const isActive = !!activeLogSource && sourceKey === activeLogSource;
                btn.classList.toggle("active", isActive);
                btn.setAttribute("aria-pressed", isActive ? "true" : "false");
            });
        }

        function ensureFileListElement() {
            if (fileList) return fileList;
            const panel = document.querySelector(".pane-primary");
            if (!panel) return null;
            const dynamicEmpty = document.getElementById("list-empty-dynamic");
            const emptyBlock = panel.querySelector(".empty");
            const list = document.createElement("ul");
            list.className = "list";
            if (dynamicEmpty && dynamicEmpty.parentElement === panel) {
                panel.insertBefore(list, dynamicEmpty);
            } else if (emptyBlock && emptyBlock.parentElement === panel) {
                panel.insertBefore(list, emptyBlock);
            } else {
                panel.appendChild(list);
            }
            fileList = list;
            if (typeof fileListScrollbarCleanup === "function") {
                fileListScrollbarCleanup();
            }
            fileListScrollbarCleanup = watchVerticalScrollbarClass(fileList);
            addScopedListener(fileList, "scroll", () => {
                persistFileViewState({ listScrollTop: fileList.scrollTop });
            });
            ensureFileListClickBinding();
            return fileList;
        }

        function ensureFileListClickBinding() {
            if (!fileList || fileListClickBound) return;
            addScopedListener(fileList, "click", async (event) => {
                const target = event.target;
                if (!(target instanceof Element)) return;
                const viewBtn = target.closest(".file-view-btn");
                if (viewBtn) {
                    event.preventDefault();
                    setDownloadError("");
                    const url = viewBtn.getAttribute("data-view-url") || "";
                    const downloadUrl = viewBtn.getAttribute("data-download-url") || "";
                    const filename = viewBtn.getAttribute("data-filename") || "File Viewer";
                    if (!url) return;
                    await runFileView({ url, downloadUrl, filename });
                    return;
                }
                const downloadBtn = target.closest(".file-download-btn");
                if (downloadBtn && !downloadBtn.classList.contains("file-download-link")) {
                    event.preventDefault();
                    setDownloadError("");
                    const url = downloadBtn.getAttribute("data-download-url") || "";
                    const filename = downloadBtn.getAttribute("data-filename") || "backup.zip";
                    if (!url) return;
                    openPasswordModal({ kind: "download", url, filename });
                    return;
                }
                const restoreBtn = target.closest(".file-restore-btn");
                if (restoreBtn) {
                    event.preventDefault();
                    if (!restoreServerIsOff) {
                        setDownloadError("Restore is disabled while server is not Off.");
                        return;
                    }
                    setDownloadError("");
                    const filename = restoreBtn.getAttribute("data-filename") || "";
                    const displayName = restoreBtn.getAttribute("data-display-name") || filename;
                    if (!filename) return;
                    openBackupRestorePane(filename, "local", displayName);
                }
            });
            fileListClickBound = true;
        }

        function isServerOffForRestore(metricsPayload) {
            const runningStatus = String(metricsPayload?.service_running_status || "").trim().toLowerCase();
            if (runningStatus === "inactive" || runningStatus === "failed") return true;
            const displayStatus = String(metricsPayload?.service_status || "").trim().toLowerCase();
            return displayStatus === "off";
        }

        function syncRestoreAvailabilityUi() {
            if (pageId !== "backups") return;
            const restoreDisabled = !restoreServerIsOff;
            const blockedTitle = "Restore is only available when server status is Off.";
            document.querySelectorAll(".file-restore-btn").forEach((btn) => {
                btn.disabled = restoreDisabled;
                btn.title = restoreDisabled ? blockedTitle : "";
            });
            if (backupRestoreStart) {
                const hasSelection = !!selectedRestoreFilename;
                backupRestoreStart.disabled = restoreDisabled || !hasSelection;
                backupRestoreStart.title = restoreDisabled ? blockedTitle : "";
            }
        }

        function openBackupRestorePane(filename, source = "local", displayName = "") {
            if (pageId !== "backups") return;
            selectedRestoreFilename = filename || "";
            selectedRestoreDisplayName = displayName || selectedRestoreFilename;
            restorePaneForcedByRemote = source === "remote";
            setActiveRestoreFilename(selectedRestoreDisplayName);
            if (restorePaneForcedByRemote) {
                stopRestorePaneAlertHeartbeat();
            } else {
                startRestorePaneAlertHeartbeat();
            }
            openViewer();
            setBackupRestoreControlsVisible(true);
            syncRestoreAvailabilityUi();
            if (fileViewerTitle) {
                fileViewerTitle.textContent = selectedRestoreDisplayName
                    ? `Restore: ${selectedRestoreDisplayName}`
                    : "Restore Backup";
            }
            if (fileViewerContent) {
                const message = selectedRestoreDisplayName
                    ? `Ready to restore ${selectedRestoreDisplayName}. Press Restore to continue.`
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

        function stopRestoreOperationPolling() {
            if (!restoreOperationPollTimer) return;
            window.clearTimeout(restoreOperationPollTimer);
            restoreOperationPollTimer = null;
            restoreOperationOpId = "";
        }

        function scheduleRestorePoll(delayMs) {
            stopRestorePolling();
            restorePollTimer = window.setTimeout(pollRestoreStatus, delayMs);
        }

        function scheduleRestoreOperationPoll(delayMs) {
            if (!restoreOperationOpId) return;
            if (restoreOperationPollTimer) {
                window.clearTimeout(restoreOperationPollTimer);
            }
            restoreOperationPollTimer = window.setTimeout(pollRestoreOperationStatus, delayMs);
        }

        async function pollRestoreOperationStatus() {
            if (pageId !== "backups" || !restoreOperationOpId) return;
            let response;
            let payload = null;
            try {
                response = await fetch(`/operation-status/${encodeURIComponent(restoreOperationOpId)}`, {
                    method: "GET",
                    headers: { "X-Requested-With": "XMLHttpRequest" },
                    cache: "no-store",
                });
            } catch (_) {
                scheduleRestoreOperationPoll(1100);
                return;
            }
            try {
                payload = await response.json();
            } catch (_) {
                payload = null;
            }
            if (!response.ok || !payload || payload.ok === false || !payload.operation) {
                scheduleRestoreOperationPoll(1300);
                return;
            }
            const operation = payload.operation || {};
            const status = String(operation.status || "").trim().toLowerCase();
            if (status === "failed") {
                appendRestoreLine("", String(operation.message || "Restore failed."));
                showErrorModal(String(operation.message || "Restore failed."), {
                    errorCode: String(operation.error_code || "restore_failed"),
                });
                stopRestoreOperationPolling();
                return;
            }
            if (status === "observed") {
                announceFileListInvalidation({ backups: true });
                stopRestoreOperationPolling();
                return;
            }
            scheduleRestoreOperationPoll(700);
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

            syncRestoreAvailabilityUi();
            if (payload.running) {
                scheduleRestorePoll(800);
            } else {
                stopRestorePolling();
                if (payload.result && payload.result.ok) {
                    announceFileListInvalidation({ backups: true });
                    setDownloadError("");
                } else if (payload.result && payload.result.message) {
                    showErrorModal(payload.result.message || "Restore failed.", {
                        errorCode: payload.result.error || "",
                    });
                    setDownloadError(payload.result.message);
                }
            }
        }

        function startRestoreProgressPanel(jobId, title, startMessage) {
            if (pageId !== "backups") return;
            restorePollJobId = jobId || "";
            restorePollSeq = 0;
            startRestorePaneAlertHeartbeat();
            ensureRestoreViewerOpen(title || "Restore Progress");
            if (fileViewerContent) fileViewerContent.innerHTML = "";
            syncRestoreAvailabilityUi();
            appendRestoreLine("", startMessage || "Restore started.");
            if (restorePollJobId) {
                scheduleRestorePoll(200);
            }
        }

        async function sendRestorePaneOpenSignal() {
            if (pageId !== "backups") return;
            if (document.hidden) return;
            const filename = (selectedRestoreFilename || activeRestoreFilename || "").trim();
            try {
                await fetch("/maintenance/nav-alert/restore-pane-open", {
                    method: "POST",
                    headers: {
                        "X-Requested-With": "XMLHttpRequest",
                        "X-CSRF-Token": csrfToken || "",
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({ filename, client_id: restorePaneClientId }),
                    cache: "no-store",
                    keepalive: true,
                });
            } catch (_) {
                // Best-effort nav attention signal.
            }
        }

        function stopRestorePaneAlertHeartbeat() {
            if (!restorePaneAlertTimer) return;
            window.clearInterval(restorePaneAlertTimer);
            restorePaneAlertTimer = null;
        }

        function startRestorePaneAlertHeartbeat() {
            if (pageId !== "backups") return;
            sendRestorePaneOpenSignal();
            if (restorePaneAlertTimer) return;
            restorePaneAlertTimer = window.setInterval(sendRestorePaneOpenSignal, 8000);
        }

        function applyRestorePaneSharedState(navAttention) {
            const payload = navAttention && typeof navAttention === "object" ? navAttention : {};
            const active = !!payload.restore_pane_attention;
            const openedBySelf = !!payload.restore_pane_opened_by_self;
            const filename = String(payload.restore_pane_filename || "").trim();
            const openerName = String(payload.restore_pane_opened_by_name || "").trim();
            remoteRestoreActive = active && !!filename && !openedBySelf;
            remoteRestoreFilename = remoteRestoreActive ? filename : "";
            remoteRestoreOpenedByName = remoteRestoreActive ? (openerName || "unknown") : "";

            if (!remoteRestoreActive && restorePaneForcedByRemote) {
                restorePaneForcedByRemote = false;
                closeViewer();
            }
            applyActiveFileRowHighlight();
        }

        function applyBackupMetricsSnapshot(payload) {
            if (!payload || typeof payload !== "object") return;
            restoreServerIsOff = isServerOffForRestore(payload);
            syncRestoreAvailabilityUi();
            applyRestorePaneSharedState(payload.nav_attention || null);
        }

        function stopFileHeartbeatPolling() {
            fileHeartbeatController.stop();
        }

        function startFileHeartbeatPolling() {
            fileHeartbeatController.start();
        }

        function handleVisibilityStateChange() {
            if (document.hidden) {
                stopFileHeartbeatPolling();
                stopRestorePaneAlertHeartbeat();
                return;
            }
            startFileHeartbeatPolling();
            if (pageId === "backups" && selectedRestoreFilename) {
                startRestorePaneAlertHeartbeat();
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
            const items = Array.from(fileList.querySelectorAll("li:not(.list-state)"));
            sortItems(items, mode).forEach((item) => fileList.appendChild(item));
        }

        function buildLogFileItemRow(item, payload) {
            const safeName = String(item?.name || "").trim();
            if (!safeName) return "";
            const nameHtml = escapeHtml(safeName);
            const nameLowerHtml = escapeHtml(safeName.toLowerCase());
            const source = encodeURIComponent(String(payload?.source || ""));
            const encodedFile = encodeURIComponent(safeName);
            const viewBase = String(payload?.view_base || "");
            const downloadBase = String(payload?.download_base || "");
            const viewUrl = viewBase ? `${viewBase}/${encodedFile}` : `/view-log-file/${source}/${encodedFile}`;
            const downloadUrl = downloadBase ? `${downloadBase}/${encodedFile}` : `/download/log-files/${source}/${encodedFile}`;
            const mtime = Number(item?.mtime || 0);
            const sizeBytes = Number(item?.size_bytes || 0);
            const modified = escapeHtml(String(item?.modified || ""));
            const sizeText = escapeHtml(String(item?.size_text || ""));
            return `
<li data-name="${nameLowerHtml}" data-filename="${nameHtml}" data-mtime="${String(mtime)}" data-size="${String(sizeBytes)}">
    <span class="file-name">${nameHtml}</span>
    <div class="file-actions">
        <a class="file-action-btn file-download-btn file-download-link" href="${downloadUrl}">Download</a>
        <button
            class="file-action-btn file-view-btn"
            type="button"
            data-view-url="${viewUrl}"
            data-download-url="${downloadUrl}"
            data-filename="${nameHtml}"
        >View</button>
    </div>
    <span class="meta">${modified} | ${sizeText}</span>
</li>`.trim();
        }

        function ensureListLoadingNode() {
            if (!fileList) return null;
            let node = document.getElementById("list-loading");
            if (node && node.parentElement === fileList) {
                listLoading = node;
                return node;
            }
            node = document.createElement("li");
            node.id = "list-loading";
            node.className = "list-state empty";
            node.textContent = "Loading...";
            fileList.prepend(node);
            listLoading = node;
            return node;
        }

        function setListLoadingState(isLoading) {
            const node = ensureListLoadingNode();
            if (!node) return;
            node.style.display = isLoading ? "block" : "none";
        }

        function toggleEmptyState(hasRows) {
            if (listEmptyDynamic) {
                listEmptyDynamic.textContent = emptyText || listEmptyDynamic.textContent || "No files found.";
                listEmptyDynamic.style.display = hasRows ? "none" : "block";
            }
            const emptyBlock = document.querySelector(".pane-primary .empty");
            if (emptyBlock && emptyBlock !== listLoading && emptyBlock !== listEmptyDynamic) {
                emptyBlock.style.display = hasRows ? "none" : "block";
            }
        }

        function listHasRows() {
            return !!fileList && !!fileList.querySelector("li:not(.list-state)");
        }

        function handleListLoadFailure(message) {
            setListLoadingState(false);
            setDownloadError(message || "Failed to load file list.");
            toggleEmptyState(listHasRows());
        }

        function restoreFileListScroll() {
            if (!fileList) return;
            const top = Number(getPersistedFileViewState().listScrollTop || 0);
            if (!Number.isFinite(top) || top <= 0) {
                fileList.scrollTop = 0;
                return;
            }
            fileList.scrollTop = top;
        }

        function logSourceTitleFor(source) {
            const sourceKey = String(source || "").trim().toLowerCase();
            const toggle = logSourceToggles.find((btn) => String(btn.getAttribute("data-log-source") || "").trim().toLowerCase() === sourceKey);
            return toggle ? String(toggle.textContent || "Log Viewer").trim() : "Log Viewer";
        }

        function restoreShellViewStateAfterListLoad() {
            if (hasRestoredShellViewState) return;
            hasRestoredShellViewState = true;
            const state = getPersistedFileViewState();
            if (!state.viewerOpen || !state.viewerRequest || typeof state.viewerRequest !== "object") return;
            if (state.viewerKind === "file") {
                const request = {
                    url: String(state.viewerRequest.url || ""),
                    downloadUrl: String(state.viewerRequest.downloadUrl || ""),
                    filename: String(state.viewerRequest.filename || ""),
                };
                if (request.url) {
                    runFileView(request, { restoreState: true }).catch(() => {});
                }
                return;
            }
            if (state.viewerKind === "log_source") {
                const source = String(state.viewerRequest.source || "").trim().toLowerCase();
                if (!source) return;
                runLogSourceView({
                    source,
                    title: String(state.viewerRequest.title || logSourceTitleFor(source) || "Log Viewer"),
                }, { restoreState: true }).catch(() => {});
            }
        }

        function buildStandardFileItemRow(item, payload) {
            const safeName = String(item?.name || "").trim();
            if (!safeName) return "";
            const nameHtml = escapeHtml(safeName);
            const nameLowerHtml = escapeHtml(safeName.toLowerCase());
            const encodedFile = encodeURIComponent(safeName);
            const downloadBase = String(payload?.download_base || "");
            const viewBase = String(payload?.view_base || "");
            const downloadUrl = downloadBase ? `${downloadBase}/${encodedFile}` : "#";
            const viewUrl = viewBase ? `${viewBase}/${encodedFile}` : "";
            const mtime = Number(item?.mtime || 0);
            const sizeBytes = Number(item?.size_bytes || 0);
            const modified = escapeHtml(String(item?.modified || ""));
            const sizeText = escapeHtml(String(item?.size_text || ""));
            if (pageId === "backups") {
                const restoreName = escapeHtml(String(item?.restore_name || item?.name || ""));
                const downloadName = escapeHtml(String(item?.download_name || item?.name || ""));
                return `
<li data-name="${nameLowerHtml}" data-filename="${nameHtml}" data-mtime="${String(mtime)}" data-size="${String(sizeBytes)}">
    <span class="file-name">${nameHtml}</span>
    <div class="file-actions">
        <button
            class="file-action-btn file-download-btn"
            type="button"
            data-download-url="${downloadUrl}"
            data-filename="${downloadName}"
        >Download</button>
        <button
            class="file-action-btn file-restore-btn"
            type="button"
            data-filename="${restoreName}"
            data-display-name="${nameHtml}"
        >Restore</button>
    </div>
    <span class="meta">${modified} | ${sizeText}</span>
</li>`.trim();
            }
            return `
<li data-name="${nameLowerHtml}" data-filename="${nameHtml}" data-mtime="${String(mtime)}" data-size="${String(sizeBytes)}">
    <span class="file-name">${nameHtml}</span>
    <div class="file-actions">
        <a class="file-action-btn file-download-btn file-download-link" href="${downloadUrl}">Download</a>
        <button
            class="file-action-btn file-view-btn"
            type="button"
            data-view-url="${viewUrl}"
            data-download-url="${downloadUrl}"
            data-filename="${nameHtml}"
        >View</button>
    </div>
    <span class="meta">${modified} | ${sizeText}</span>
</li>`.trim();
        }

        function announceFileListInvalidation(detail = {}) {
            if (detail.backups && shell && typeof shell.invalidateFilePageListCache === "function") {
                shell.invalidateFilePageListCache("backups");
            }
            if (detail.logFiles && shell && typeof shell.invalidateLogFileListCache === "function") {
                shell.invalidateLogFileListCache(detail.logFiles === true ? "" : detail.logFiles);
            }
            window.dispatchEvent(new CustomEvent(FILE_LISTS_INVALIDATED_EVENT, { detail }));
        }

        function renderStandardFileList(payload) {
            setDownloadError("");
            const list = ensureFileListElement();
            if (!list) return;
            const items = Array.isArray(payload?.items) ? payload.items : [];
            const rows = items.map((item) => buildStandardFileItemRow(item, payload)).filter(Boolean);
            list.innerHTML = rows.join("\n");
            setListLoadingState(false);
            toggleEmptyState(rows.length > 0);
            if (pageId === "backups") {
                applyBackupSortAndFilter();
            } else {
                applyFileSort(sortSelect ? (sortSelect.value || "newest") : "newest");
            }
            applyActiveFileRowHighlight();
            syncRestoreAvailabilityUi();
            restoreFileListScroll();
            restoreShellViewStateAfterListLoad();
        }

        async function loadStandardFileList(options = {}) {
            if (!listApiPath || pageId === "minecraft_logs") return;
            const loadToken = nextFileListLoadToken();
            setListLoadingState(true);
            try {
                const payload = dataClient && typeof dataClient.loadStandardFileList === "function"
                    ? await dataClient.loadStandardFileList({ force: !!options.force })
                    : (shell && typeof shell.fetchFilePageItems === "function")
                        ? await shell.fetchFilePageItems(pageId, listApiPath, { force: !!options.force })
                        : await fetch(listApiPath, {
                            method: "GET",
                            headers: { "X-Requested-With": "XMLHttpRequest" },
                            cache: "no-store",
                        }).then((response) => response.ok ? response.json() : Promise.reject(new Error("load_failed")));
                if (!isCurrentFileListLoadToken(loadToken)) return;
                renderStandardFileList(payload);
            } catch (_) {
                if (!isCurrentFileListLoadToken(loadToken)) return;
                handleListLoadFailure("Failed to load file list.");
            }
        }

        function renderLogFileList(payload) {
            setDownloadError("");
            const list = ensureFileListElement();
            if (!list) return;
            const items = Array.isArray(payload?.items) ? payload.items : [];
            const rows = items.map((item) => buildLogFileItemRow(item, payload)).filter(Boolean);
            list.innerHTML = rows.join("\n");
            toggleEmptyState(rows.length > 0);
            setListLoadingState(false);
            applyFileSort(sortSelect ? (sortSelect.value || "newest") : "newest");
            setActiveViewedFilename("");
            applyActiveFileRowHighlight();
            syncRestoreAvailabilityUi();
            restoreFileListScroll();
            restoreShellViewStateAfterListLoad();
        }

        async function loadLogFileSourceList(source, options = {}) {
            const sourceKey = String(source || "").trim().toLowerCase();
            if (!sourceKey) return;
            const loadToken = nextFileListLoadToken();
            setListLoadingState(true);
            try {
                const payload = dataClient && typeof dataClient.loadLogFileList === "function"
                    ? await dataClient.loadLogFileList(sourceKey, { force: !!options.force })
                    : (shell && typeof shell.fetchLogFileList === "function")
                        ? await shell.fetchLogFileList(sourceKey, { force: !!options.force })
                        : await fetch(`/log-files/${encodeURIComponent(sourceKey)}`, {
                            method: "GET",
                            headers: { "X-Requested-With": "XMLHttpRequest" },
                            cache: "no-store",
                        }).then((response) => response.ok ? response.json() : Promise.reject(new Error("load_failed")));
                if (!isCurrentFileListLoadToken(loadToken)) return;
                currentLogFileSource = String(payload.source || sourceKey);
                persistFileViewState({ currentLogFileSource });
                setActiveLogSource(currentLogFileSource);
                renderLogFileList(payload);
            } catch (_) {
                if (!isCurrentFileListLoadToken(loadToken)) return;
                handleListLoadFailure("Failed to load log file list.");
            }
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
            const items = Array.from(fileList.querySelectorAll("li:not(.list-state)"));
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
            persistFileViewState({
                backupSortMode: selectedSort,
                backupFilters: Object.fromEntries(
                    backupFilterInputs.map((input) => [input.value, !!input.checked])
                ),
            });
        }

        function formatViewerLogHtml(rawText) {
            return logUtils.formatBracketAwareLogHtml(rawText, { highlightErrorLine: true });
        }

        const viewerController = (viewerRuntime && typeof viewerRuntime.createViewerController === "function")
            ? viewerRuntime.createViewerController({
                wrap,
                fileViewer,
                fileViewerResizer,
                paneAnimations,
                viewerWidthStorageKey,
                viewerHeightStorageKey,
                paneAnimationDurationMs: PANE_ANIMATION_DURATION_MS,
            })
            : null;

        function closeViewer() {
            selectedRestoreFilename = "";
            selectedRestoreDisplayName = "";
            restorePaneForcedByRemote = false;
            setBackupRestoreControlsVisible(false);
            setActiveViewedFilename("");
            setActiveRestoreFilename("");
            persistFileViewState({ viewerOpen: false });
            if (viewerController) {
                viewerController.close();
            }
            if (fileViewerTitle) {
                fileViewerTitle.textContent = "File Viewer";
            }
        }

        function openViewer() {
            persistFileViewState({ viewerOpen: true });
            if (viewerController) {
                viewerController.open();
            }
        }

        function openPasswordModal(actionRequest) {
            if (!passwordModal || !passwordInput) return;
            pendingAction = actionRequest;
            if (passwordTitle) {
                if (actionRequest.kind === "restore") {
                    passwordTitle.textContent = "Confirm Restore";
                } else {
                    passwordTitle.textContent = "Enter Password";
                }
            }
            if (passwordText) {
                if (actionRequest.kind === "restore") {
                    const restoreDisplay = actionRequest.displayName || actionRequest.filename;
                    passwordText.textContent = `Enter sudo password to restore ${restoreDisplay}. This will create a new world folder and switch level-name.`;
                } else {
                    passwordText.textContent = "Enter sudo password to download this backup.";
                }
            }
            if (passwordSubmit) {
                if (actionRequest.kind === "restore") {
                    passwordSubmit.textContent = "Restore";
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
            closeSuccessModal();
            closeErrorModal();
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

        function showSuccessModal(message) {
            closePasswordModal();
            closeMessageModal();
            closeErrorModal();
            if (!successModal || !successModalText) return;
            successModalText.textContent = message || "Action completed successfully.";
            successModal.classList.add("open");
            successModal.setAttribute("aria-hidden", "false");
        }

        function closeSuccessModal() {
            if (!successModal) return;
            successModal.classList.remove("open");
            successModal.setAttribute("aria-hidden", "true");
        }

        function showErrorModal(message, options = {}) {
            closePasswordModal();
            closeSuccessModal();
            const code = String(options.errorCode || "").trim();
            if (!errorModal || !errorModalText) {
                setDownloadError(message || "Action failed.");
                return;
            }
            const detail = code ? `${message || "Action failed."} (error: ${code})` : (message || "Action failed.");
            errorModalText.textContent = detail;
            errorModal.classList.add("open");
            errorModal.setAttribute("aria-hidden", "false");
        }

        function closeErrorModal() {
            if (!errorModal) return;
            errorModal.classList.remove("open");
            errorModal.setAttribute("aria-hidden", "true");
        }

        async function runBackupDownload(downloadRequest, password) {
            let response;
            let payload = null;
            try {
                if (http) {
                    const result = await http.postUrlEncoded(
                        downloadRequest.url,
                        {
                            csrf_token: csrfToken || "",
                            sudo_password: password,
                        },
                        {
                            csrfToken,
                            headers: { "X-Requested-With": "XMLHttpRequest" },
                        }
                    );
                    response = result.response;
                    payload = result.payload;
                } else {
                    const body = new URLSearchParams();
                    body.set("csrf_token", csrfToken || "");
                    body.set("sudo_password", password);
                    response = await fetch(downloadRequest.url, {
                        method: "POST",
                        headers: {
                            "X-Requested-With": "XMLHttpRequest",
                            "X-CSRF-Token": csrfToken || "",
                            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                        },
                        body: body.toString(),
                    });
                }
            } catch (err) {
                showErrorModal("Download failed. Please try again.", { errorCode: "network_error" });
                setDownloadError("Download failed. Please try again.");
                return;
            }

            if (!response.ok) {
                let message = "Password incorrect. Download cancelled.";
                let errorCode = "";
                if (!payload) {
                    try {
                        payload = await response.json();
                    } catch (_) {
                        payload = {};
                    }
                }
                if (payload && payload.message) message = payload.message;
                if (payload && payload.error) errorCode = payload.error;
                if (errorCode === "password_incorrect") {
                    showMessageModal(message);
                } else {
                    showErrorModal(message, { errorCode });
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
            showSuccessModal(`Download started for ${downloadRequest.filename}.`);
        }

        async function runBackupRestore(restoreRequest, password) {
            let response;
            let payload = null;
            try {
                if (http) {
                    const result = await http.postUrlEncoded(
                        "/restore-backup",
                        {
                            csrf_token: csrfToken || "",
                            sudo_password: password,
                            filename: restoreRequest.filename || "",
                        },
                        {
                            csrfToken,
                            headers: { "X-Requested-With": "XMLHttpRequest" },
                        }
                    );
                    response = result.response;
                    payload = result.payload;
                } else {
                    const body = new URLSearchParams();
                    body.set("csrf_token", csrfToken || "");
                    body.set("sudo_password", password);
                    body.set("filename", restoreRequest.filename || "");
                    response = await fetch("/restore-backup", {
                        method: "POST",
                        headers: {
                            "X-Requested-With": "XMLHttpRequest",
                            "X-CSRF-Token": csrfToken || "",
                            "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                        },
                        body: body.toString(),
                    });
                }
            } catch (err) {
                showErrorModal("Restore failed. Please try again.", { errorCode: "network_error" });
                setDownloadError("Restore failed. Please try again.");
                return;
            }

            if (!payload) {
                try {
                    payload = await response.json();
                } catch (_) {
                    payload = null;
                }
            }

            if (!response.ok) {
                const message = (payload && payload.message) ? payload.message : "Restore failed.";
                const errorCode = (payload && payload.error) ? payload.error : "";
                if (errorCode === "password_incorrect") {
                    showMessageModal(message);
                } else {
                    showErrorModal(message, { errorCode });
                    setDownloadError(message);
                }
                return;
            }

            const jobId = (payload && payload.job_id) ? payload.job_id : "";
            const opId = (payload && payload.op_id) ? payload.op_id : "";
            const restoreDisplay = restoreRequest.displayName || restoreRequest.filename || "selected backup";
            startRestoreProgressPanel(jobId, "Restore Progress", `Restore requested for ${restoreDisplay}.`);
            if (opId) {
                restoreOperationOpId = String(opId || "").trim();
                scheduleRestoreOperationPoll(500);
            }
            showSuccessModal(`Restore requested for ${restoreDisplay}.`);
        }

        async function runFileView(viewRequest, options = {}) {
            if (!fileViewer || !fileViewerContent || !fileViewerTitle) return;
            if (pageId !== "minecraft_logs") {
                setActiveLogSource("");
            }
            setActiveViewedFilename(viewRequest.filename || "");
            fileViewerTitle.textContent = viewRequest.filename || "File Viewer";
            fileViewerContent.textContent = "Loading...";
            const normalizedRequest = {
                url: viewRequest.url || "",
                downloadUrl: viewRequest.downloadUrl || "",
                filename: viewRequest.filename || "",
            };
            setViewerDownloadMode("download_viewed", "Download", false, {
                downloadUrl: normalizedRequest.downloadUrl,
                filename: normalizedRequest.filename,
            });
            persistFileViewState({ viewerKind: "file", viewerRequest: normalizedRequest, viewerOpen: true });
            openViewer();

            let payload = null;
            try {
                payload = dataClient && typeof dataClient.loadViewedFile === "function"
                    ? await dataClient.loadViewedFile(viewRequest.url)
                    : (shell && typeof shell.fetchViewedFile === "function")
                        ? await shell.fetchViewedFile(viewRequest.url)
                        : await fetch(viewRequest.url, {
                            method: "GET",
                            headers: {
                                "X-Requested-With": "XMLHttpRequest",
                            },
                            cache: "no-store",
                        }).then((response) => response.ok ? response.json() : Promise.reject(new Error("Failed to load file.")));
            } catch (_) {
                fileViewerContent.textContent = "Failed to load file.";
                return;
            }

            if (!payload || !payload.ok) {
                const message = (payload && payload.message) ? payload.message : "Failed to load file.";
                fileViewerContent.innerHTML = formatViewerLogHtml(message);
                return;
            }
            fileViewerTitle.textContent = payload.filename || viewRequest.filename || "File Viewer";
            setActiveViewedFilename(payload.filename || viewRequest.filename || "");
            fileViewerContent.innerHTML = formatViewerLogHtml(payload.content || "");
            const restoredTop = options.restoreState ? Number(getPersistedFileViewState().viewerScrollTop || 0) : 0;
            fileViewerContent.scrollTop = restoredTop;
            persistFileViewState({ viewerScrollTop: fileViewerContent.scrollTop });
            setViewerDownloadMode("download_viewed", "Download", true, {
                downloadUrl: viewRequest.downloadUrl || "",
                filename: payload.filename || viewRequest.filename || "",
            });
        }

        async function runLogSourceView(logRequest, options = {}) {
            if (!fileViewer || !fileViewerContent || !fileViewerTitle) return;
            const source = String(logRequest?.source || "").trim();
            const title = String(logRequest?.title || "Log Viewer").trim();
            if (!source) return;
            setActiveViewedFilename("");
            setActiveLogSource(source);
            fileViewerTitle.textContent = title;
            fileViewerContent.textContent = "Loading...";
            const normalizedRequest = {
                source,
                title,
            };
            setViewerDownloadMode("", "Download", false, {});
            persistFileViewState({ viewerKind: "log_source", viewerRequest: normalizedRequest, viewerOpen: true });
            openViewer();

            try {
                const logs = (shell && typeof shell.fetchLogText === "function")
                    ? String(await shell.fetchLogText(source) || "(no logs)")
                    : await fetch(`/log-text/${encodeURIComponent(source)}`, {
                        method: "GET",
                        headers: { "X-Requested-With": "XMLHttpRequest" },
                        cache: "no-store",
                    }).then((response) => response.ok ? response.json() : null)
                        .then((payload) => String((payload && payload.logs) || "(no logs)"));
                fileViewerContent.innerHTML = formatViewerLogHtml(logs);
                if (options.restoreState) {
                    fileViewerContent.scrollTop = Number(getPersistedFileViewState().viewerScrollTop || 0);
                } else {
                    fileViewerContent.scrollTop = fileViewerContent.scrollHeight;
                }
                persistFileViewState({ viewerScrollTop: fileViewerContent.scrollTop });
            } catch (_) {
                fileViewerContent.innerHTML = formatViewerLogHtml("Failed to load log source.");
            }
        }

        if (passwordCancel) {
            addScopedListener(passwordCancel, "click", () => {
                closePasswordModal();
            });
        }
        if (passwordModal) {
            addScopedListener(passwordModal, "click", (event) => {
                if (event.target === passwordModal) {
                    closePasswordModal();
                }
            });
        }
        if (messageModal) {
            addScopedListener(messageModal, "click", (event) => {
                if (event.target === messageModal) {
                    closeMessageModal();
                }
            });
        }
        if (messageModalOk) {
            addScopedListener(messageModalOk, "click", () => {
                closeMessageModal();
            });
        }
        if (successModal) {
            addScopedListener(successModal, "click", (event) => {
                if (event.target === successModal) {
                    closeSuccessModal();
                }
            });
        }
        if (successModalOk) {
            addScopedListener(successModalOk, "click", () => {
                closeSuccessModal();
            });
        }
        if (errorModal) {
            addScopedListener(errorModal, "click", (event) => {
                if (event.target === errorModal) {
                    closeErrorModal();
                }
            });
        }
        if (errorModalOk) {
            addScopedListener(errorModalOk, "click", () => {
                closeErrorModal();
            });
        }
        if (passwordSubmit) {
            addScopedListener(passwordSubmit, "click", async () => {
                if (!passwordInput || !pendingAction) return;
                const password = (passwordInput.value || "").trim();
                if (!password) return;
                const action = pendingAction;
                closePasswordModal();
                if (action.kind === "restore") {
                    await runBackupRestore(action, password);
                    return;
                }
                await runBackupDownload(action, password);
            });
        }
        if (passwordInput) {
            addScopedListener(passwordInput, "keydown", (event) => {
                if (event.key === "Enter" && passwordSubmit) {
                    event.preventDefault();
                    passwordSubmit.click();
                }
            });
        }
        if (fileViewerClose) {
            addScopedListener(fileViewerClose, "click", closeViewer);
        }
        if (backupRestoreStart) {
            addScopedListener(backupRestoreStart, "click", () => {
                if (pageId !== "backups" || !selectedRestoreFilename) return;
                if (!restoreServerIsOff) {
                    setDownloadError("Restore is disabled while server is not Off.");
                    return;
                }
                setDownloadError("");
                openPasswordModal({
                    kind: "restore",
                    filename: selectedRestoreFilename,
                    displayName: selectedRestoreDisplayName,
                });
            });
        }
        if (backupRestoreCancel) {
            addScopedListener(backupRestoreCancel, "click", () => {
                closeViewer();
            });
        }
        if (fileViewerResizer) {
            addScopedListener(fileViewerResizer, "pointerdown", (event) => viewerController && viewerController.startResize(event));
            addScopedListener(window, "pointermove", (event) => {
                if (!viewerController || !viewerController.isResizing()) return;
                viewerController.handlePointerMove(event);
            });
            addScopedListener(window, "pointerup", () => viewerController && viewerController.stopResize());
            addScopedListener(window, "pointercancel", () => viewerController && viewerController.stopResize());
            addScopedListener(window, "blur", () => viewerController && viewerController.stopResize());
        }
        addScopedListener(window, "resize", () => {
            if (viewerController) {
                viewerController.syncLayout();
            }
        });
        if (viewerController) {
            viewerController.loadStoredSize();
        }
        const initialFileViewState = getPersistedFileViewState();
        if (sortSelect && initialFileViewState.sortMode) {
            sortSelect.value = initialFileViewState.sortMode;
        }
        if (backupSortSelect && initialFileViewState.backupSortMode) {
            backupSortSelect.value = initialFileViewState.backupSortMode;
        }
        if (initialFileViewState.backupFilters) {
            backupFilterInputs.forEach((input) => {
                if (Object.prototype.hasOwnProperty.call(initialFileViewState.backupFilters, input.value)) {
                    input.checked = !!initialFileViewState.backupFilters[input.value];
                }
            });
        }
        activeViewedFilename = String(initialFileViewState.activeViewedFilename || "");
        activeRestoreFilename = String(initialFileViewState.activeRestoreFilename || "");
        activeLogSource = String(initialFileViewState.activeLogSource || "").trim().toLowerCase();
        currentLogFileSource = String(initialFileViewState.currentLogFileSource || "").trim().toLowerCase();
        if (sortSelect) {
            addScopedListener(sortSelect, "change", () => {
                const nextSort = sortSelect.value || "newest";
                persistFileViewState({ sortMode: nextSort });
                applyFileSort(nextSort);
            });
            applyFileSort(sortSelect.value || "newest");
        }
        if (backupSortSelect) {
            addScopedListener(backupSortSelect, "change", applyBackupSortAndFilter);
            backupFilterInputs.forEach((input) => {
                addScopedListener(input, "change", applyBackupSortAndFilter);
            });
            applyBackupSortAndFilter();
        }
        if (fileViewerDownload) {
            addScopedListener(fileViewerDownload, "click", () => {
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

        if (pageId === "backups") {
            syncRestoreAvailabilityUi();
            if (shell && typeof shell.subscribeMetrics === "function") {
                fileMetricsUnsubscribe = shell.subscribeMetrics((payload) => applyBackupMetricsSnapshot(payload));
            } else if (window.__MCWEB_LAST_METRICS_SNAPSHOT && typeof window.__MCWEB_LAST_METRICS_SNAPSHOT === "object") {
                applyBackupMetricsSnapshot(window.__MCWEB_LAST_METRICS_SNAPSHOT);
            }
            addScopedListener(window, "beforeunload", stopRestoreOperationPolling);
        }
        function handleFileListInvalidated(event) {
            const detail = event && event.detail && typeof event.detail === "object" ? event.detail : {};
            if (pageId === "backups" && detail.backups) {
                loadStandardFileList({ force: true });
                return;
            }
            if (pageId === "minecraft_logs" && detail.logFiles) {
                const source = currentLogFileSource || activeLogSource || initialLogFileSource || "minecraft";
                loadLogFileSourceList(source, { force: true });
            }
        }
        addScopedListener(window, FILE_LISTS_INVALIDATED_EVENT, handleFileListInvalidated);
        if (pageId === "backups") {
            loadStandardFileList();
        }
        // Release page-local timers and subscriptions before the shell swaps
        // this fragment out or the browser unloads.
        function teardownFilePageLifecycle() {
            pageRuntimeActive = false;
            fileListLoadToken += 1;
            stopFileHeartbeatPolling();
            stopRestorePaneAlertHeartbeat();
            stopRestorePolling();
            stopRestoreOperationPolling();
            if (typeof fileMetricsUnsubscribe === "function") {
                fileMetricsUnsubscribe();
                fileMetricsUnsubscribe = null;
            }
            if (typeof fileViewerScrollbarCleanup === "function") {
                fileViewerScrollbarCleanup();
                fileViewerScrollbarCleanup = null;
            }
            if (viewerController) {
                viewerController.teardown();
            }
            if (typeof fileListScrollbarCleanup === "function") {
                fileListScrollbarCleanup();
                fileListScrollbarCleanup = null;
            }
            if (cleanup && typeof cleanup.run === "function") {
                cleanup.run();
            }
            if (teardownFileBrowserPage === teardownFilePageLifecycle) {
                teardownFileBrowserPage = null;
            }
        }
        teardownFileBrowserPage = teardownFilePageLifecycle;

        addScopedListener(document, "visibilitychange", handleVisibilityStateChange);
        addScopedListener(window, "pagehide", teardownFilePageLifecycle);
        ensureFileListClickBinding();
        logSourceToggles.forEach((btn) => {
            addScopedListener(btn, "click", async () => {
                setDownloadError("");
                const source = btn.getAttribute("data-log-source") || "";
                if (!source) return;
                await loadLogFileSourceList(source);
            });
        });
        if (pageId === "minecraft_logs") {
            currentLogFileSource = initialLogFileSource || currentLogFileSource || "minecraft";
            persistFileViewState({ currentLogFileSource });
            setActiveLogSource(activeLogSource || currentLogFileSource || "minecraft");
            if (logSourceToggles.length > 0) {
                loadLogFileSourceList(currentLogFileSource);
            }
        }
    return teardownFileBrowserPage;
}

if (pageModules && typeof pageModules.register === "function") {
    pageModules.register(["backups", "minecraft_logs"], {
        mount: mountFileBrowserPage,
        unmount: function () {
            if (typeof teardownFileBrowserPage === "function") {
                teardownFileBrowserPage();
            }
        },
    });
}

// Direct full-page loads still boot here for non-shell compatibility.
if (!document.getElementById("mcweb-app-content")) {
    mountFileBrowserPage();
}




