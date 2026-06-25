(function () {
    var root = document.documentElement;
    var themeToggle = document.getElementById("light-dark-mode");

    function getCookie(name) {
        var cookieValue = null;
        if (!document.cookie) {
            return cookieValue;
        }
        var cookies = document.cookie.split(";");
        for (var i = 0; i < cookies.length; i += 1) {
            var cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === name + "=") {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
        return cookieValue;
    }

    function getCsrfToken() {
        var metaTag = document.querySelector('meta[name="csrf-token"]');
        if (metaTag) {
            var token = (metaTag.getAttribute("content") || "").trim();
            if (token && token !== "NOTPROVIDED") {
                return token;
            }
        }
        return getCookie("csrftoken");
    }

    function syncStartbarTheme() {
        var currentTheme = root.getAttribute("data-bs-theme") || "light";
        root.setAttribute("data-startbar", currentTheme);
        return currentTheme;
    }

    function persistTheme(theme) {
        var csrfToken = getCsrfToken();
        var headers = {
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest"
        };
        if (csrfToken) {
            headers["X-CSRFToken"] = csrfToken;
        }

        return fetch("/api/user/settings/theme/", {
            method: "PATCH",
            credentials: "same-origin",
            headers: headers,
            body: JSON.stringify({ theme: theme })
        });
    }

    var lastTheme = syncStartbarTheme();

    function persistCurrentTheme() {
        var theme = syncStartbarTheme();
        if (theme === lastTheme) {
            return;
        }
        lastTheme = theme;
        persistTheme(theme).catch(function () {
            return null;
        });
    }

    // Persist whenever app.js updates the root theme attribute.
    if (typeof MutationObserver !== "undefined") {
        var observer = new MutationObserver(function (mutations) {
            for (var i = 0; i < mutations.length; i += 1) {
                if (mutations[i].attributeName === "data-bs-theme") {
                    persistCurrentTheme();
                    break;
                }
            }
        });
        observer.observe(root, { attributes: true, attributeFilter: ["data-bs-theme"] });
    }

    // Fallback when MutationObserver isn't available.
    if (themeToggle) {
        themeToggle.addEventListener("click", function () {
            window.setTimeout(persistCurrentTheme, 0);
        });
    }
})();
