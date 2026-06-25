(function () {
    if (window.htmx) {
        return;
    }

    var panels = document.querySelectorAll("[data-refresh-url]");
    if (!panels.length) {
        return;
    }

    function refreshPanel(panel) {
        var url = panel.getAttribute("data-refresh-url");
        if (!url) {
            return;
        }

        fetch(url, {
            credentials: "same-origin",
            headers: { "X-Requested-With": "XMLHttpRequest" },
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("Refresh request failed.");
                }
                return response.text();
            })
            .then(function (html) {
                panel.innerHTML = html;
            })
            .catch(function () {
                panel.setAttribute("data-refresh-state", "failed");
            });
    }

    panels.forEach(function (panel) {
        var interval = parseInt(panel.getAttribute("data-refresh-interval") || "10000", 10);
        refreshPanel(panel);
        window.setInterval(function () {
            refreshPanel(panel);
        }, Math.max(interval, 3000));
    });
})();
