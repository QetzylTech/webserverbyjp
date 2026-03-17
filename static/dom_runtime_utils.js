(function (global) {
    function syncVerticalScrollbarClass(target) {
        if (!target) return;
        const hasVerticalScrollbar = target.scrollHeight > target.clientHeight + 1;
        target.classList.toggle("has-vscroll", hasVerticalScrollbar);
    }

    function watchVerticalScrollbarClass(target, options = {}) {
        if (!target) return () => {};
        syncVerticalScrollbarClass(target);

        const cleanupFns = [];
        const handleScroll = () => syncVerticalScrollbarClass(target);
        const handleResize = () => syncVerticalScrollbarClass(target);

        target.addEventListener("scroll", handleScroll, { passive: true });
        cleanupFns.push(() => target.removeEventListener("scroll", handleScroll));

        global.addEventListener("resize", handleResize);
        cleanupFns.push(() => global.removeEventListener("resize", handleResize));

        if (global.ResizeObserver) {
            const resizeObserver = new ResizeObserver(() => syncVerticalScrollbarClass(target));
            resizeObserver.observe(target);
            cleanupFns.push(() => resizeObserver.disconnect());
        }

        if (options.observeMutations && global.MutationObserver) {
            const mutationObserver = new MutationObserver(() => syncVerticalScrollbarClass(target));
            mutationObserver.observe(target, { childList: true, subtree: true, characterData: true });
            cleanupFns.push(() => mutationObserver.disconnect());
        }

        return () => {
            cleanupFns.splice(0).reverse().forEach((fn) => {
                try {
                    fn();
                } catch (_) {
                    // Ignore observer/listener teardown failures.
                }
            });
        };
    }

    function createCleanupStack() {
        const cleanupFns = [];

        function add(fn) {
            if (typeof fn === "function") {
                cleanupFns.push(fn);
            }
        }

        function listen(target, type, handler, options) {
            if (!target || typeof target.addEventListener !== "function") return;
            target.addEventListener(type, handler, options);
            add(() => {
                try {
                    target.removeEventListener(type, handler, options);
                } catch (_) {
                    // Ignore listener teardown failures.
                }
            });
        }

        function listenMedia(mql, handler) {
            if (!mql || !handler) return;
            if (typeof mql.addEventListener === "function") {
                mql.addEventListener("change", handler);
                add(() => {
                    try {
                        mql.removeEventListener("change", handler);
                    } catch (_) {
                        // Ignore listener teardown failures.
                    }
                });
            } else if (typeof mql.addListener === "function") {
                mql.addListener(handler);
                add(() => {
                    try {
                        mql.removeListener(handler);
                    } catch (_) {
                        // Ignore listener teardown failures.
                    }
                });
            }
        }

        function timeout(fn, delayMs) {
            const id = global.setTimeout(fn, delayMs);
            add(() => global.clearTimeout(id));
            return id;
        }

        function interval(fn, delayMs) {
            const id = global.setInterval(fn, delayMs);
            add(() => global.clearInterval(id));
            return id;
        }

        function run() {
            cleanupFns.splice(0).reverse().forEach((fn) => {
                try {
                    fn();
                } catch (_) {
                    // Ignore cleanup failures.
                }
            });
        }

        return {
            add,
            listen,
            listenMedia,
            timeout,
            interval,
            run,
        };
    }
    global.MCWebDomUtils = Object.assign({}, global.MCWebDomUtils || {}, {
        syncVerticalScrollbarClass,
        watchVerticalScrollbarClass,
        createCleanupStack,
    });
})(window);




