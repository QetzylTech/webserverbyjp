(function (global) {
    function createViewerController(options) {
        const wrap = options.wrap || null;
        const fileViewer = options.fileViewer || null;
        const fileViewerResizer = options.fileViewerResizer || null;
        const paneAnimations = options.paneAnimations || null;
        const viewerWidthStorageKey = String(options.viewerWidthStorageKey || "");
        const viewerHeightStorageKey = String(options.viewerHeightStorageKey || "");
        const paneAnimationDurationMs = Number(options.paneAnimationDurationMs || 220);

        let isResizing = false;
        let viewerCloseTimer = null;

        function isStackedLayout() {
            return global.innerWidth <= 1100;
        }

        function clearPaneAnimation(target) {
            if (!paneAnimations) return;
            paneAnimations.clearPaneAnimation(target);
        }

        function playPaneAnimation(target, direction, animationOptions = {}) {
            if (!paneAnimations) return;
            paneAnimations.playPaneAnimation(target, direction, isStackedLayout(), animationOptions);
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
            const maxPx = Math.max(380, Math.floor(global.innerWidth * 0.75));
            return Math.max(minPx, Math.min(maxPx, Math.round(px)));
        }

        function clampViewerHeight(px) {
            const minPx = 220;
            const maxPx = Math.max(280, Math.floor(global.innerHeight * 0.75));
            return Math.max(minPx, Math.min(maxPx, Math.round(px)));
        }

        function applyViewerWidth(px) {
            if (!wrap) return;
            const clamped = clampViewerWidth(px);
            wrap.style.setProperty("--viewer-width", `${clamped}px`);
            try {
                global.localStorage.setItem(viewerWidthStorageKey, String(clamped));
            } catch (_) {
                // Ignore storage failures.
            }
        }

        function applyViewerHeight(px) {
            if (!wrap) return;
            const clamped = clampViewerHeight(px);
            wrap.style.setProperty("--viewer-height", `${clamped}px`);
            try {
                global.localStorage.setItem(viewerHeightStorageKey, String(clamped));
            } catch (_) {
                // Ignore storage failures.
            }
        }

        function loadViewerWidth() {
            if (!wrap) return;
            let saved = "";
            try {
                saved = global.localStorage.getItem(viewerWidthStorageKey) || "";
            } catch (_) {
                saved = "";
            }
            const parsed = Number(saved);
            if (Number.isFinite(parsed) && parsed > 0) {
                applyViewerWidth(parsed);
                return;
            }
            applyViewerWidth(Math.floor(global.innerWidth * 0.4));
        }

        function loadViewerHeight() {
            if (!wrap) return;
            let saved = "";
            try {
                saved = global.localStorage.getItem(viewerHeightStorageKey) || "";
            } catch (_) {
                saved = "";
            }
            const parsed = Number(saved);
            if (Number.isFinite(parsed) && parsed > 0) {
                applyViewerHeight(parsed);
                return;
            }
            applyViewerHeight(Math.floor(global.innerHeight * 0.42));
        }

        function updateViewerWidthFromPointer(clientX) {
            if (!wrap) return;
            const desired = global.innerWidth - clientX - 12;
            applyViewerWidth(desired);
        }

        function updateViewerHeightFromPointer(clientY) {
            if (!wrap) return;
            const wrapRect = wrap.getBoundingClientRect();
            const desired = wrapRect.bottom - clientY - 6;
            applyViewerHeight(desired);
        }

        function stopResize() {
            if (!isResizing) return;
            isResizing = false;
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
            if (fileViewerResizer) {
                fileViewerResizer.classList.remove("is-dragging");
            }
        }

        function startResize(event) {
            if (!fileViewerResizer || !wrap || !wrap.classList.contains("viewer-open")) return;
            isResizing = true;
            document.body.style.cursor = isStackedLayout() ? "row-resize" : "col-resize";
            document.body.style.userSelect = "none";
            fileViewerResizer.classList.add("is-dragging");
            if (isStackedLayout()) {
                updateViewerHeightFromPointer(event.clientY);
            } else {
                updateViewerWidthFromPointer(event.clientX);
            }
            event.preventDefault();
        }

        function handlePointerMove(event) {
            if (!isResizing) return;
            if (isStackedLayout()) {
                updateViewerHeightFromPointer(event.clientY);
            } else {
                updateViewerWidthFromPointer(event.clientX);
            }
        }

        function close() {
            if (!wrap || !fileViewer) return;
            if (viewerCloseTimer) {
                global.clearTimeout(viewerCloseTimer);
                viewerCloseTimer = null;
            }
            const finishClose = () => {
                fileViewer.setAttribute("aria-hidden", "true");
                wrap.classList.remove("viewer-open", "viewer-closing");
                clearPaneAnimation(fileViewer);
                clearFloatingPaneStyles(fileViewer);
                viewerCloseTimer = null;
            };
            if (!wrap.classList.contains("viewer-open")) {
                finishClose();
                return;
            }
            floatPaneForClose(fileViewer);
            wrap.classList.add("viewer-closing");
            playPaneAnimation(fileViewer, "out", { keepClassOnEnd: true });
            viewerCloseTimer = global.setTimeout(finishClose, paneAnimationDurationMs + 20);
        }

        function open() {
            if (!wrap || !fileViewer) return;
            if (viewerCloseTimer) {
                global.clearTimeout(viewerCloseTimer);
                viewerCloseTimer = null;
            }
            clearFloatingPaneStyles(fileViewer);
            clearPaneAnimation(fileViewer);
            fileViewer.setAttribute("aria-hidden", "false");
            wrap.classList.remove("viewer-closing");
            wrap.classList.add("viewer-open");
            if (paneAnimations) {
                playPaneAnimation(fileViewer, "in");
            }
        }

        function syncLayout() {
            if (isStackedLayout()) {
                const currentHeight = parseInt(global.getComputedStyle(wrap).getPropertyValue("--viewer-height"), 10);
                if (Number.isFinite(currentHeight) && currentHeight > 0) {
                    applyViewerHeight(currentHeight);
                }
                return;
            }
            const currentWidth = parseInt(global.getComputedStyle(wrap).getPropertyValue("--viewer-width"), 10);
            if (Number.isFinite(currentWidth) && currentWidth > 0) {
                applyViewerWidth(currentWidth);
            }
        }

        function loadStoredSize() {
            loadViewerWidth();
            loadViewerHeight();
        }

        function teardown() {
            stopResize();
            if (viewerCloseTimer) {
                global.clearTimeout(viewerCloseTimer);
                viewerCloseTimer = null;
            }
        }

        return {
            open,
            close,
            startResize,
            handlePointerMove,
            stopResize,
            isResizing: () => isResizing,
            isStackedLayout,
            applyViewerWidth,
            applyViewerHeight,
            loadStoredSize,
            syncLayout,
            teardown,
        };
    }

    global.MCWebFileViewerRuntime = Object.assign({}, global.MCWebFileViewerRuntime || {}, {
        createViewerController,
    });
})(window);
