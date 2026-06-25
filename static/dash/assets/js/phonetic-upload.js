(function () {
    var form = document.getElementById("phonetic-case-form");
    if (!form) {
        return;
    }

    var statusEl = document.getElementById("upload-status");
    var sampleAInput = form.querySelector('input[name="sample_a"]');
    var sampleBInput = form.querySelector('input[name="sample_b"]');
    var sampleAProgress = document.getElementById("sample-a-progress");
    var sampleBProgress = document.getElementById("sample-b-progress");
    var submitButton = document.getElementById("phonetic-submit-button");

    function setProgress(element, value) {
        var safeValue = Math.max(0, Math.min(100, Math.round(value)));
        element.style.width = safeValue + "%";
        element.textContent = safeValue + "%";
    }

    function setStatus(message, level) {
        statusEl.className = "alert mb-0 alert-" + level;
        statusEl.textContent = message;
    }

    function setSubmitting(isSubmitting) {
        if (!submitButton) {
            return;
        }

        submitButton.disabled = isSubmitting;
        submitButton.textContent = isSubmitting ? "Uploading evidence..." : "Create phonetic case";
    }

    function parseErrors(errors) {
        var messages = [];
        Object.keys(errors || {}).forEach(function (key) {
            if (key === "__all__") {
                messages.push(errors[key].join(", "));
                return;
            }
            messages.push(key.replaceAll("_", " ") + ": " + errors[key].join(", "));
        });
        return messages.join(" | ");
    }

    function validateExtension(file) {
        if (!file) {
            return false;
        }

        var name = file.name.toLowerCase();
        return name.endsWith(".wav") || name.endsWith(".mp3");
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        if (!sampleAInput.files.length || !sampleBInput.files.length) {
            setStatus("Both Sample A and Sample B files are required.", "danger");
            return;
        }
        if (!validateExtension(sampleAInput.files[0]) || !validateExtension(sampleBInput.files[0])) {
            setStatus("Only .wav and .mp3 files are accepted for phonetic evidence uploads.", "danger");
            return;
        }

        var formData = new FormData(form);
        var xhr = new XMLHttpRequest();
        var sampleASize = sampleAInput.files[0].size || 1;
        var sampleBSize = sampleBInput.files[0].size || 1;
        var totalFiles = sampleASize + sampleBSize;

        xhr.open("POST", form.action, true);
        xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");

        xhr.upload.addEventListener("progress", function (progressEvent) {
            if (!progressEvent.lengthComputable) {
                return;
            }

            var metadataAllowance = 1024 * 32;
            var adjustedLoaded = Math.max(progressEvent.loaded - metadataAllowance, 0);
            var ratio = adjustedLoaded / totalFiles;
            var fileALoaded = Math.min(sampleASize, adjustedLoaded);
            var fileBLoaded = Math.max(Math.min(adjustedLoaded - sampleASize, sampleBSize), 0);
            setProgress(sampleAProgress, (fileALoaded / sampleASize) * 100);
            setProgress(sampleBProgress, (fileBLoaded / sampleBSize) * 100);
            setStatus("Uploading forensic evidence... " + Math.round(ratio * 100) + "%", "info");
        });

        xhr.addEventListener("load", function () {
            if (xhr.status >= 200 && xhr.status < 300) {
                var payload = JSON.parse(xhr.responseText || "{}");
                setProgress(sampleAProgress, 100);
                setProgress(sampleBProgress, 100);
                setStatus("Upload complete. Redirecting to the case workspace.", "success");
                if (payload.redirect_url) {
                    window.location.assign(payload.redirect_url);
                }
                return;
            }

            try {
                var errorPayload = JSON.parse(xhr.responseText || "{}");
                setStatus(parseErrors(errorPayload.errors), "danger");
            } catch (error) {
                setStatus("Upload failed. Please review the files and try again.", "danger");
            }
            setSubmitting(false);
        });

        xhr.addEventListener("error", function () {
            setStatus("Upload failed because the request could not be completed.", "danger");
            setSubmitting(false);
        });

        xhr.addEventListener("abort", function () {
            setStatus("Upload was interrupted before completion.", "warning");
            setSubmitting(false);
        });

        setSubmitting(true);
        setStatus("Preparing multipart upload request.", "secondary");
        xhr.send(formData);
    });
})();
