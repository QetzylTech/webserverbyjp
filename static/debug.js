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

    const cfg = window.__MCWEB_DEBUG_CONFIG || {};
    const csrfToken = cfg.csrfToken || "";

    const toggle = document.getElementById("nav-toggle");
    const sidebar = document.getElementById("side-nav");
    const backdrop = document.getElementById("nav-backdrop");
    if (toggle && sidebar && backdrop) {
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
    }

    document.querySelectorAll(".debug-row-reset").forEach((btn) => {
        btn.addEventListener("click", () => {
            const targetName = btn.getAttribute("data-reset-target") || "";
            const original = btn.getAttribute("data-original") || "";
            if (!targetName) return;
            const input = document.querySelector(`input[name="${targetName}"]`);
            if (!input) return;
            input.value = original;
        });
    });

    const backupMode = document.getElementById("debug-backup-mode");
    const backupMinutes = document.getElementById("debug-backup-minutes");
    const backupForm = document.getElementById("debug-backup-form");
    function updateBackupModeUi() {
        if (!backupMode || !backupMinutes) return;
        const scheduled = backupMode.value === "scheduled";
        backupMinutes.style.display = scheduled ? "" : "none";
        backupMinutes.required = scheduled;
    }
    if (backupMode) {
        backupMode.addEventListener("change", updateBackupModeUi);
        updateBackupModeUi();
    }
    if (backupForm) {
        backupForm.addEventListener("submit", (event) => {
            if (!backupMode || !backupMinutes) return;
            if (backupMode.value === "scheduled" && !String(backupMinutes.value || "").trim()) {
                event.preventDefault();
                backupMinutes.focus();
            }
        });
    }

    const debugShell = document.getElementById("debug-shell");
    const viewer = document.getElementById("debug-viewer");
    const viewerTitle = document.getElementById("debug-viewer-title");
    const viewerResizer = document.getElementById("debug-viewer-resizer");
    const viewerPath = document.getElementById("debug-viewer-path");
    const propsForm = document.getElementById("debug-props-form");
    const propsError = document.getElementById("debug-props-error");
    const openEditorBtn = document.getElementById("open-props-editor");
    const closeViewerBtn = document.getElementById("debug-viewer-close");
    const applyViewerBtn = document.getElementById("debug-viewer-apply");
    const resetViewerBtn = document.getElementById("debug-viewer-reset");
    const viewerPasswordInput = document.getElementById("debug-viewer-password");
    const openExplorerBtn = document.getElementById("open-file-explorer");
    const explorerDrawer = document.getElementById("debug-explorer-drawer");
    const explorerClose = document.getElementById("debug-explorer-close");
    const explorerViewSwitch = document.getElementById("debug-explorer-view-switch");
    const explorerListViewBtn = document.getElementById("debug-explorer-list-view");
    const explorerGridViewBtn = document.getElementById("debug-explorer-grid-view");
    const explorerPath = document.getElementById("debug-explorer-path");
    const explorerUp = document.getElementById("debug-explorer-up");
    const explorerRefresh = document.getElementById("debug-explorer-refresh");
    const explorerList = document.getElementById("debug-explorer-list");
    const mainPane = document.querySelector(".debug-main");
    const envPanel = document.querySelector(".debug-env-panel");
    const envTableWrap = document.querySelector(".debug-table-wrap");
    const EXPLORER_ROOT_KEY = "minecraft";
    const viewerWidthStorageKey = "mcweb.debug.viewerWidth";
    const explorerWidthStorageKey = "mcweb.debug.explorerWidth";
    const viewerHeightStorageKey = "mcweb.debug.viewerHeight";
    const PANE_ANIMATION_DURATION_MS = 220;
    const paneAnimations = window.MCWebPaneAnimations || null;
    let explorerViewMode = "list";
    let explorerEntriesCache = [];
    let propsRowsCache = [];

    if (viewer) viewer.style.display = "none";
    if (explorerDrawer) explorerDrawer.style.display = "none";

    let isResizing = false;
    let viewerCloseTimer = null;
    let explorerCloseTimer = null;
    function setExplorerViewMode(mode) {
        explorerViewMode = mode === "grid" ? "grid" : "list";
        if (explorerViewSwitch) {
            explorerViewSwitch.setAttribute("data-mode", explorerViewMode);
        }
        if (explorerListViewBtn) explorerListViewBtn.classList.toggle("active", explorerViewMode === "list");
        if (explorerGridViewBtn) explorerGridViewBtn.classList.toggle("active", explorerViewMode === "grid");
    }
    setExplorerViewMode("list");

    function runWithoutPaneAnimation(action) {
        if (!debugShell) {
            action();
            return;
        }
        debugShell.classList.add("pane-switching");
        action();
        window.requestAnimationFrame(() => {
            debugShell.classList.remove("pane-switching");
        });
    }
    function openViewer() {
        if (!debugShell || !viewer) return;
        if (viewerCloseTimer) {
            window.clearTimeout(viewerCloseTimer);
            viewerCloseTimer = null;
        }
        clearFloatingPaneStyles(viewer);
        ensureMainPaneVisible();
        const alreadyOpen = debugShell.classList.contains("viewer-open");
        const switchingFromExplorer = debugShell.classList.contains("explorer-open");
        const open = () => {
            closeExplorerDrawer({ immediate: switchingFromExplorer });
            clearPaneAnimation(viewer);
            debugShell.classList.add("viewer-open");
            viewer.setAttribute("aria-hidden", "false");
            viewer.style.display = "flex";
            if (!alreadyOpen && !switchingFromExplorer) {
                playPaneAnimation(viewer, "in");
            }
        };
        if (switchingFromExplorer) {
            runWithoutPaneAnimation(open);
            return;
        }
        open();
    }
    function closeViewer(options = {}) {
        if (!debugShell || !viewer) return;
        if (viewerCloseTimer) {
            window.clearTimeout(viewerCloseTimer);
            viewerCloseTimer = null;
        }
        const immediate = !!options.immediate;
        const finishClose = () => {
            debugShell.classList.remove("viewer-open");
            viewer.setAttribute("aria-hidden", "true");
            clearPaneAnimation(viewer);
            clearFloatingPaneStyles(viewer);
            viewer.style.display = "none";
            ensureMainPaneVisible();
        };
        if (immediate || !debugShell.classList.contains("viewer-open")) {
            finishClose();
            return;
        }
        playPaneAnimation(viewer, "out", { keepClassOnEnd: true });
        viewerCloseTimer = window.setTimeout(finishClose, PANE_ANIMATION_DURATION_MS + 20);
    }
    function openExplorerDrawer() {
        if (!explorerDrawer || !debugShell) return;
        if (explorerCloseTimer) {
            window.clearTimeout(explorerCloseTimer);
            explorerCloseTimer = null;
        }
        clearFloatingPaneStyles(explorerDrawer);
        ensureMainPaneVisible();
        const alreadyOpen = debugShell.classList.contains("explorer-open");
        const switchingFromViewer = debugShell.classList.contains("viewer-open");
        const open = () => {
            closeViewer({ immediate: switchingFromViewer });
            clearPaneAnimation(explorerDrawer);
            debugShell.classList.add("explorer-open");
            explorerDrawer.setAttribute("aria-hidden", "false");
            explorerDrawer.style.display = "flex";
            if (!alreadyOpen && !switchingFromViewer) {
                playPaneAnimation(explorerDrawer, "in");
            }
        };
        if (switchingFromViewer) {
            runWithoutPaneAnimation(open);
            return;
        }
        open();
    }
    function closeExplorerDrawer(options = {}) {
        if (!explorerDrawer || !debugShell) return;
        if (explorerCloseTimer) {
            window.clearTimeout(explorerCloseTimer);
            explorerCloseTimer = null;
        }
        const immediate = !!options.immediate;
        const finishClose = () => {
            debugShell.classList.remove("explorer-open");
            explorerDrawer.setAttribute("aria-hidden", "true");
            clearPaneAnimation(explorerDrawer);
            clearFloatingPaneStyles(explorerDrawer);
            explorerDrawer.style.display = "none";
            ensureMainPaneVisible();
        };
        if (immediate || !debugShell.classList.contains("explorer-open")) {
            finishClose();
            return;
        }
        playPaneAnimation(explorerDrawer, "out", { keepClassOnEnd: true });
        explorerCloseTimer = window.setTimeout(finishClose, PANE_ANIMATION_DURATION_MS + 20);
    }
    function isStackedPaneLayout() {
        return window.innerWidth <= 1100;
    }
    function clearPaneAnimation(target) {
        if (!paneAnimations) return;
        paneAnimations.clearPaneAnimation(target);
    }
    function playPaneAnimation(target, direction, options = {}) {
        if (!paneAnimations) return;
        paneAnimations.playPaneAnimation(target, direction, isStackedPaneLayout(), options);
    }
    function floatPaneForClose(target) {
        if (!paneAnimations) return;
        paneAnimations.floatPaneForClose(target);
    }
    function clearFloatingPaneStyles(target) {
        if (!paneAnimations) return;
        paneAnimations.clearFloatingPaneStyles(target);
    }
    function ensureMainPaneVisible() {
        if (!mainPane) return;
        mainPane.style.display = "grid";
        mainPane.style.visibility = "visible";
        mainPane.style.opacity = "1";
        mainPane.style.transform = "";
    }
    function isViewerPaneOpen() {
        return !!(debugShell && debugShell.classList.contains("viewer-open"));
    }
    function isExplorerPaneOpen() {
        return !!(debugShell && debugShell.classList.contains("explorer-open"));
    }
    function clampViewerWidth(px) {
        const minPx = 360;
        const maxPx = Math.max(420, Math.floor(window.innerWidth * 0.8));
        return Math.max(minPx, Math.min(maxPx, Math.round(px)));
    }
    function clampExplorerWidth(px) {
        const minPx = 360;
        const maxPx = Math.max(420, Math.floor(window.innerWidth * 0.8));
        return Math.max(minPx, Math.min(maxPx, Math.round(px)));
    }
    function clampViewerHeight(px) {
        const minPx = 220;
        const maxPx = Math.max(280, Math.floor(window.innerHeight * 0.75));
        return Math.max(minPx, Math.min(maxPx, Math.round(px)));
    }
    function applyViewerWidth(px) {
        if (!debugShell) return;
        const clamped = clampViewerWidth(px);
        debugShell.style.setProperty("--viewer-width", `${clamped}px`);
        try {
            localStorage.setItem(viewerWidthStorageKey, String(clamped));
        } catch (_) {
            // Ignore storage failures.
        }
    }
    function applyExplorerWidth(px) {
        if (!debugShell) return;
        const clamped = clampExplorerWidth(px);
        debugShell.style.setProperty("--explorer-width", `${clamped}px`);
        try {
            localStorage.setItem(explorerWidthStorageKey, String(clamped));
        } catch (_) {
            // Ignore storage failures.
        }
    }
    function applyViewerHeight(px) {
        if (!debugShell) return;
        const clamped = clampViewerHeight(px);
        debugShell.style.setProperty("--viewer-height", `${clamped}px`);
        try {
            localStorage.setItem(viewerHeightStorageKey, String(clamped));
        } catch (_) {
            // Ignore storage failures.
        }
    }
    function loadViewerWidth() {
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
        applyViewerWidth(Math.floor(window.innerWidth * 0.42));
    }
    function loadExplorerWidth() {
        let saved = "";
        try {
            saved = localStorage.getItem(explorerWidthStorageKey) || "";
        } catch (_) {
            saved = "";
        }
        const parsed = Number(saved);
        if (Number.isFinite(parsed) && parsed > 0) {
            applyExplorerWidth(parsed);
            return;
        }
        applyExplorerWidth(Math.floor(window.innerWidth * 0.42));
    }
    function loadViewerHeight() {
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
    function updateViewerWidthFromPointer(clientX) {
        const desired = window.innerWidth - clientX - 12;
        applyViewerWidth(desired);
    }
    function updateExplorerWidthFromPointer(clientX) {
        const desired = window.innerWidth - clientX - 12;
        applyExplorerWidth(desired);
    }
    function updateViewerHeightFromPointer(clientY) {
        if (!debugShell) return;
        const shellRect = debugShell.getBoundingClientRect();
        const desired = shellRect.bottom - clientY - 6;
        applyViewerHeight(desired);
    }
    function stopResize() {
        if (!isResizing) return;
        isResizing = false;
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        if (viewerResizer) {
            viewerResizer.classList.remove("is-dragging");
        }
    }
    function startResize(event) {
        if (!viewerResizer || !debugShell) return;
        const stacked = isStackedPaneLayout();
        const viewerOpen = isViewerPaneOpen();
        const explorerOpen = isExplorerPaneOpen();
        if (!viewerOpen && !explorerOpen) return;
        isResizing = true;
        document.body.style.cursor = stacked ? "row-resize" : "col-resize";
        document.body.style.userSelect = "none";
        viewerResizer.classList.add("is-dragging");
        if (stacked) {
            updateViewerHeightFromPointer(event.clientY);
        } else if (viewerOpen) {
            updateViewerWidthFromPointer(event.clientX);
        } else {
            updateExplorerWidthFromPointer(event.clientX);
        }
        event.preventDefault();
    }
    if (viewerResizer) {
        viewerResizer.addEventListener("pointerdown", startResize);
        window.addEventListener("pointermove", (event) => {
            if (!isResizing) return;
            if (isStackedPaneLayout()) {
                updateViewerHeightFromPointer(event.clientY);
                return;
            }
            if (isViewerPaneOpen()) {
                updateViewerWidthFromPointer(event.clientX);
                return;
            }
            if (isExplorerPaneOpen()) {
                updateExplorerWidthFromPointer(event.clientX);
            }
        });
        window.addEventListener("pointerup", stopResize);
        window.addEventListener("pointercancel", stopResize);
        window.addEventListener("blur", stopResize);
    }
    window.addEventListener("resize", () => {
        if (isStackedPaneLayout()) {
            const currentHeight = parseFloat((debugShell && debugShell.style.getPropertyValue("--viewer-height")) || "0");
            if (Number.isFinite(currentHeight) && currentHeight > 0) {
                applyViewerHeight(currentHeight);
            }
            return;
        }
        const currentViewerWidth = parseFloat((debugShell && debugShell.style.getPropertyValue("--viewer-width")) || "0");
        if (Number.isFinite(currentViewerWidth) && currentViewerWidth > 0) {
            applyViewerWidth(currentViewerWidth);
        }
        const currentExplorerWidth = parseFloat((debugShell && debugShell.style.getPropertyValue("--explorer-width")) || "0");
        if (Number.isFinite(currentExplorerWidth) && currentExplorerWidth > 0) {
            applyExplorerWidth(currentExplorerWidth);
        }
    });
    loadViewerWidth();
    loadExplorerWidth();
    loadViewerHeight();

    function escapeHtml(text) {
        return String(text || "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");
    }

    function setPropsError(message) {
        if (!propsError) return;
        if (message) {
            propsError.hidden = false;
            propsError.textContent = message;
            return;
        }
        propsError.hidden = true;
        propsError.textContent = "";
    }

    function renderPropsRows(rows) {
        if (!propsForm) return;
        const items = Array.isArray(rows) ? rows : [];
        if (!items.length) {
            propsForm.innerHTML = "<p class=\"debug-props-original\">No editable settings found.</p>";
            return;
        }
        let html = "<table class=\"debug-props-table\"><thead><tr><th>Key</th><th>Value</th><th>File value</th></tr></thead><tbody>";
        for (const row of items) {
            const key = escapeHtml(row.key || "");
            const value = String(row.value || "");
            const original = escapeHtml(row.original || "");
            const editable = row.editable !== false;
            const rowType = row.type || "string";
            const options = Array.isArray(row.options) ? row.options : [];
            let inputHtml = "";
            if (!editable) {
                inputHtml = `<input class="debug-props-input" data-prop-key="${key}" value="${escapeHtml(value)}" disabled>`;
            } else if (rowType === "bool") {
                const selectedTrue = value.toLowerCase() === "true" ? " selected" : "";
                const selectedFalse = value.toLowerCase() === "false" ? " selected" : "";
                inputHtml = `
                    <select class="debug-props-input" data-prop-key="${key}">
                        <option value="true"${selectedTrue}>true</option>
                        <option value="false"${selectedFalse}>false</option>
                    </select>
                `;
            } else if (rowType === "enum") {
                const opts = options.map((opt) => {
                    const selected = opt === value ? " selected" : "";
                    return `<option value="${escapeHtml(opt)}"${selected}>${escapeHtml(opt)}</option>`;
                }).join("");
                inputHtml = `<select class="debug-props-input" data-prop-key="${key}">${opts}</select>`;
            } else if (rowType === "int") {
                inputHtml = `<input class="debug-props-input" data-prop-key="${key}" type="number" value="${escapeHtml(value)}">`;
            } else {
                inputHtml = `<input class="debug-props-input" data-prop-key="${key}" type="text" value="${escapeHtml(value)}">`;
            }
            const fixedBadge = editable ? "" : ` <span class="debug-props-fixed">FORCED</span>`;
            html += `<tr><td class="debug-props-key">${key}${fixedBadge}</td><td>${inputHtml}</td><td class="debug-props-original">${original}</td></tr>`;
        }
        html += "</tbody></table>";
        propsForm.innerHTML = html;
    }

    async function loadServerProperties() {
        if (!propsForm) return;
        propsForm.innerHTML = "<p class=\"debug-props-original\">Loading...</p>";
        setPropsError("");
        if (viewerTitle) viewerTitle.textContent = "server.properties editor";
        openViewer();
        let response;
        try {
            response = await fetch("/debug/server-properties", {
                method: "GET",
                headers: { "X-Requested-With": "XMLHttpRequest" },
                cache: "no-store",
            });
        } catch (_) {
            propsForm.innerHTML = "<p class=\"debug-props-original\">Failed to load server.properties.</p>";
            return;
        }
        let payload = null;
        try {
            payload = await response.json();
        } catch (_) {
            payload = null;
        }
        if (!response.ok || !payload || !payload.ok) {
            propsForm.innerHTML = `<p class="debug-props-original">${escapeHtml((payload && payload.message) ? payload.message : "Failed to load server.properties.")}</p>`;
            return;
        }
        propsRowsCache = Array.isArray(payload.rows) ? payload.rows : [];
        renderPropsRows(propsRowsCache);
        if (viewerPath) viewerPath.textContent = payload.path || "server.properties";
    }

    function explorerIconSvg(entry) {
        const kind = (entry && entry.kind) || "file";
        const iconPath = (name) => `/static/icons/whitesur/${name}.svg`;
        if (kind === "dir") {
            return `<img class="debug-explorer-icon-img" src="${iconPath("folder")}" alt="" loading="lazy" decoding="async">`;
        }
        const name = String((entry && entry.name) || "").toLowerCase();
        if (name.endsWith(".zip") || name.endsWith(".gz") || name.endsWith(".tar")) {
            return `<img class="debug-explorer-icon-img" src="${iconPath("archive")}" alt="" loading="lazy" decoding="async">`;
        }
        if (name.endsWith(".png") || name.endsWith(".jpg") || name.endsWith(".jpeg") || name.endsWith(".gif") || name.endsWith(".webp") || name.endsWith(".svg")) {
            return `<img class="debug-explorer-icon-img" src="${iconPath("image")}" alt="" loading="lazy" decoding="async">`;
        }
        if (name.endsWith(".log") || name.endsWith(".txt") || name.endsWith(".md")) {
            return `<img class="debug-explorer-icon-img" src="${iconPath("text")}" alt="" loading="lazy" decoding="async">`;
        }
        return `<img class="debug-explorer-icon-img" src="${iconPath("file")}" alt="" loading="lazy" decoding="async">`;
    }

    function renderExplorerEntries(payload) {
        if (!explorerList) return;
        explorerList.innerHTML = "";
        explorerList.classList.toggle("grid", explorerViewMode === "grid");
        const entries = Array.isArray(payload.entries) ? payload.entries : [];
        if (!entries.length) {
            const empty = document.createElement("div");
            empty.className = "debug-explorer-empty";
            empty.textContent = "No entries in this directory.";
            explorerList.appendChild(empty);
            return;
        }
        entries.forEach((entry) => {
            const row = document.createElement("button");
            row.type = "button";
            row.className = "debug-explorer-item";
            row.setAttribute("data-kind", entry.kind || "file");
            row.setAttribute("data-rel-path", entry.rel_path || "");
            const kind = entry.kind === "dir" ? "DIR" : "FILE";
            const iconSvg = explorerIconSvg(entry);
            row.innerHTML = `
                ${iconSvg}
                <span class="debug-explorer-name">${entry.name || ""}</span>
            `;
            row.setAttribute("title", `${kind}: ${entry.name || ""}`);
            row.addEventListener("dblclick", () => {
                const rel = row.getAttribute("data-rel-path") || "";
                const kindVal = row.getAttribute("data-kind") || "file";
                if (kindVal !== "dir") return;
                if (explorerPath) explorerPath.value = rel;
                loadExplorerDirectory();
            });
            explorerList.appendChild(row);
        });
    }

    async function loadExplorerDirectory() {
        if (!explorerList) return;
        const path = explorerPath ? (explorerPath.value || "").trim() : "";
        explorerList.innerHTML = `<div class="debug-explorer-empty">Loading...</div>`;
        let response;
        try {
            const params = new URLSearchParams();
            params.set("root", EXPLORER_ROOT_KEY);
            if (path) params.set("path", path);
            response = await fetch(`/debug/explorer/list?${params.toString()}`, {
                method: "GET",
                headers: { "X-Requested-With": "XMLHttpRequest" },
                cache: "no-store",
            });
        } catch (_) {
            explorerList.innerHTML = `<div class="debug-explorer-empty">Failed to load directory.</div>`;
            return;
        }
        let payload = null;
        try {
            payload = await response.json();
        } catch (_) {
            payload = null;
        }
        if (!response.ok || !payload || !payload.ok) {
            const msg = (payload && payload.message) ? payload.message : "Failed to load directory.";
            explorerList.innerHTML = `<div class="debug-explorer-empty">${msg}</div>`;
            explorerEntriesCache = [];
            return;
        }
        if (explorerPath) explorerPath.value = payload.current_rel_path || "";
        explorerEntriesCache = Array.isArray(payload.entries) ? payload.entries : [];
        renderExplorerEntries(payload);
    }

    async function saveServerProperties() {
        if (!propsForm) return;
        const password = String(viewerPasswordInput ? viewerPasswordInput.value || "" : "").trim();
        if (!password) {
            setPropsError("Password is required to apply changes.");
            if (viewerPasswordInput) viewerPasswordInput.focus();
            return;
        }
        const body = new URLSearchParams();
        body.set("csrf_token", csrfToken || "");
        body.set("sudo_password", password);
        const inputs = propsForm.querySelectorAll("[data-prop-key]");
        inputs.forEach((el) => {
            if (el.disabled) return;
            const key = el.getAttribute("data-prop-key") || "";
            if (!key) return;
            body.set(`prop_${key}`, el.value || "");
        });
        setPropsError("");
        let response;
        try {
            response = await fetch("/debug/server-properties", {
                method: "POST",
                headers: {
                    "X-Requested-With": "XMLHttpRequest",
                    "X-CSRF-Token": csrfToken || "",
                    "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                },
                body: body.toString(),
            });
        } catch (_) {
            alert("Save failed.");
            return;
        }
        let payload = null;
        try {
            payload = await response.json();
        } catch (_) {
            payload = null;
        }
        if (!response.ok || !payload || !payload.ok) {
            const details = Array.isArray(payload && payload.errors) && payload.errors.length
                ? ` ${payload.errors.slice(0, 5).join(" | ")}`
                : "";
            setPropsError(`${(payload && payload.message) ? payload.message : "Save failed."}${details}`);
            return;
        }
        if (viewerPath && payload.path) viewerPath.textContent = payload.path;
        await loadServerProperties();
    }

    function resetServerPropertiesEditor() {
        if (!propsForm) return;
        setPropsError("");
        renderPropsRows(propsRowsCache);
    }

    if (openEditorBtn) openEditorBtn.addEventListener("click", loadServerProperties);
    if (closeViewerBtn) closeViewerBtn.addEventListener("click", closeViewer);
    if (applyViewerBtn) applyViewerBtn.addEventListener("click", saveServerProperties);
    if (resetViewerBtn) resetViewerBtn.addEventListener("click", resetServerPropertiesEditor);
    if (openExplorerBtn) {
        openExplorerBtn.addEventListener("click", () => {
            openExplorerDrawer();
        });
    }
    if (explorerClose) explorerClose.addEventListener("click", closeExplorerDrawer);

    if (explorerListViewBtn) {
        explorerListViewBtn.addEventListener("click", () => {
            setExplorerViewMode("list");
            renderExplorerEntries({ entries: explorerEntriesCache });
        });
    }
    if (explorerGridViewBtn) {
        explorerGridViewBtn.addEventListener("click", () => {
            setExplorerViewMode("grid");
            renderExplorerEntries({ entries: explorerEntriesCache });
        });
    }
    if (explorerRefresh) explorerRefresh.addEventListener("click", loadExplorerDirectory);
    if (explorerUp) {
        explorerUp.addEventListener("click", () => {
            if (!explorerPath) return;
            const current = (explorerPath.value || "").trim().replace(/\\/g, "/");
            if (!current) {
                loadExplorerDirectory();
                return;
            }
            const parts = current.split("/").filter(Boolean);
            parts.pop();
            explorerPath.value = parts.join("/");
            loadExplorerDirectory();
        });
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

    function syncEnvScrollbarPadding() {
        if (!envPanel || !envTableWrap) return;
        const hasVerticalScrollbar = envTableWrap.scrollHeight > envTableWrap.clientHeight + 1;
        envPanel.classList.toggle("has-scrollbar", hasVerticalScrollbar);
    }
    if (envTableWrap) {
        syncEnvScrollbarPadding();
        envTableWrap.addEventListener("scroll", syncEnvScrollbarPadding, { passive: true });
        window.addEventListener("resize", syncEnvScrollbarPadding);
        if (window.ResizeObserver) {
            const ro = new ResizeObserver(syncEnvScrollbarPadding);
            ro.observe(envTableWrap);
            if (envPanel) ro.observe(envPanel);
        }
    }
    watchVerticalScrollbarClass(explorerList);
    watchVerticalScrollbarClass(propsForm);

    loadExplorerDirectory();
})();
