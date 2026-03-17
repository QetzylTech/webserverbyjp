(function (global) {
    function createFilePageDataClient(options) {
        const shell = options && options.shell ? options.shell : null;
        const pageId = String(options && options.pageId ? options.pageId : "").trim().toLowerCase();
        const listApiPath = String(options && options.listApiPath ? options.listApiPath : "").trim();

        async function loadStandardFileList(options = {}) {
            if (!listApiPath || pageId === "minecraft_logs") return null;
            if (shell && typeof shell.fetchFilePageItems === "function") {
                return shell.fetchFilePageItems(pageId, listApiPath, { force: !!options.force });
            }
            const response = await fetch(listApiPath, {
                method: "GET",
                headers: { "X-Requested-With": "XMLHttpRequest" },
                cache: "no-store",
            });
            if (!response.ok) throw new Error("Failed to load file list.");
            return response.json();
        }

        async function loadLogFileList(source, options = {}) {
            const sourceKey = String(source || "").trim().toLowerCase();
            if (!sourceKey) return null;
            if (shell && typeof shell.fetchLogFileList === "function") {
                return shell.fetchLogFileList(sourceKey, { force: !!options.force });
            }
            const response = await fetch(`/log-files/${encodeURIComponent(sourceKey)}`, {
                method: "GET",
                headers: { "X-Requested-With": "XMLHttpRequest" },
                cache: "no-store",
            });
            if (!response.ok) throw new Error("Failed to load log file list.");
            return response.json();
        }

        async function loadViewedFile(url) {
            const path = String(url || "").trim();
            if (!path) throw new Error("Failed to load file.");
            if (shell && typeof shell.fetchViewedFile === "function") {
                return shell.fetchViewedFile(path);
            }
            const response = await fetch(path, {
                method: "GET",
                headers: { "X-Requested-With": "XMLHttpRequest" },
                cache: "no-store",
            });
            if (!response.ok) throw new Error("Failed to load file.");
            return response.json();
        }

        return {
            loadStandardFileList: loadStandardFileList,
            loadLogFileList: loadLogFileList,
            loadViewedFile: loadViewedFile,
        };
    }

    global.MCWebFilePageDataRuntime = Object.assign({}, global.MCWebFilePageDataRuntime || {}, {
        createFilePageDataClient: createFilePageDataClient,
    });
})(window);
