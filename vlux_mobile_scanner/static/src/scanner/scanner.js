(() => {
    "use strict";

    const body = document.body;
    const dbName = body.dataset.db || "";
    const initialPairCode = (body.dataset.pairCode || "").trim().toUpperCase();
    const tokenKey = `vlux_mobile_scanner_token_${dbName || "default"}`;

    const el = Object.fromEntries([
        "pairPanel", "scannerPanel", "pairCode", "pairButton", "pairError",
        "connectionBadge", "connectionText", "posName", "cameraVideo",
        "cameraPlaceholder", "cameraButton", "pauseButton", "httpsWarning",
        "resultIcon", "resultTitle", "resultCode", "resultProduct", "resultMessage",
        "manualBarcode", "manualSendButton", "disconnectButton",
    ].map((id) => [id, document.getElementById(id)]));

    const state = {
        token: sessionStorage.getItem(tokenKey) || "",
        posName: "",
        cooldownMs: 1500,
        stream: null,
        detector: null,
        detectionTimer: null,
        cameraRunning: false,
        sending: false,
        lastCode: "",
        lastAcceptedAt: 0,
        heartbeatTimer: null,
        audioContext: null,
    };

    function apiUrl(path, params = {}) {
        const url = new URL(path, window.location.origin);
        if (dbName) url.searchParams.set("db", dbName);
        for (const [key, value] of Object.entries(params)) {
            if (value !== undefined && value !== null && value !== "") {
                url.searchParams.set(key, value);
            }
        }
        return url.toString();
    }

    async function requestJson(path, options = {}, auth = true, query = {}) {
        const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
        if (auth && state.token) headers.Authorization = `Bearer ${state.token}`;
        const response = await fetch(apiUrl(path, query), {
            cache: "no-store",
            ...options,
            headers,
        });
        let data;
        try { data = await response.json(); }
        catch { data = { ok: false, message: `HTTP ${response.status}` }; }
        if (!response.ok || data.ok === false) {
            const error = new Error(data.message || `HTTP ${response.status}`);
            error.code = data.code || "HTTP_ERROR";
            error.status = response.status;
            throw error;
        }
        return data;
    }

    function setConnection(mode, text) {
        el.connectionBadge.classList.remove("is-online", "is-sending", "is-reconnecting", "is-offline");
        el.connectionBadge.classList.add(`is-${mode}`);
        el.connectionText.textContent = text;
    }

    function setPairError(message = "") {
        el.pairError.textContent = message;
        el.pairError.classList.toggle("is-hidden", !message);
    }

    function showScanner(posName) {
        el.pairPanel.classList.add("is-hidden");
        el.scannerPanel.classList.remove("is-hidden");
        el.posName.textContent = posName || "Caja";
        setConnection("online", "Conectado");
    }

    function showPairing() {
        stopCamera();
        el.scannerPanel.classList.add("is-hidden");
        el.pairPanel.classList.remove("is-hidden");
        setConnection("offline", "Desconectado");
    }

    function setResult(type, title, code = "", product = "", message = "") {
        const icons = { idle: "—", sending: "↗", success: "✓", error: "✕" };
        el.resultIcon.textContent = icons[type] || "—";
        el.resultIcon.className = `vlux-result-icon is-${type}`;
        el.resultTitle.textContent = title;
        el.resultCode.textContent = code;
        el.resultProduct.textContent = product;
        el.resultMessage.textContent = message;
    }

    function ensureAudioContext() {
        if (!state.audioContext) {
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            if (AudioContextClass) state.audioContext = new AudioContextClass();
        }
        if (state.audioContext?.state === "suspended") state.audioContext.resume().catch(() => {});
    }

    function beep(success = true) {
        try {
            ensureAudioContext();
            if (!state.audioContext) return;
            const oscillator = state.audioContext.createOscillator();
            const gain = state.audioContext.createGain();
            oscillator.type = "sine";
            oscillator.frequency.value = success ? 880 : 220;
            gain.gain.value = 0.05;
            oscillator.connect(gain);
            gain.connect(state.audioContext.destination);
            oscillator.start();
            oscillator.stop(state.audioContext.currentTime + (success ? 0.09 : 0.16));
        } catch { /* audio opcional */ }
    }

    function feedback(success) {
        if (navigator.vibrate) navigator.vibrate(success ? 70 : [60, 40, 60]);
        beep(success);
    }

    async function pair(code) {
        const clean = (code || "").trim().toUpperCase();
        if (clean.length !== 6) {
            setPairError("Introduce un codigo de conexion de 6 caracteres.");
            return;
        }
        setPairError("");
        el.pairButton.disabled = true;
        el.pairButton.textContent = "Conectando...";
        try {
            const data = await requestJson(
                "/vlux/mobile/pair",
                { method: "POST", body: JSON.stringify({ code: clean }) },
                false
            );
            state.token = data.token;
            state.posName = data.pos_name;
            state.cooldownMs = Number(data.cooldown_ms || 1500);
            sessionStorage.setItem(tokenKey, state.token);
            showScanner(state.posName);
            startHeartbeat();
            setResult("idle", "Telefono vinculado", "", "", "Ya puedes escanear productos para esta caja.");
        } catch (error) {
            setPairError(error.message || "No fue posible conectar con la caja.");
        } finally {
            el.pairButton.disabled = false;
            el.pairButton.textContent = "Conectar con la caja";
        }
    }

    async function heartbeat() {
        if (!state.token) return false;
        try {
            const data = await requestJson("/vlux/mobile/heartbeat", { method: "GET" });
            state.posName = data.pos_name;
            state.cooldownMs = Number(data.cooldown_ms || state.cooldownMs);
            showScanner(state.posName);
            return true;
        } catch {
            setConnection("reconnecting", "Reconectando");
            return false;
        }
    }

    function startHeartbeat() {
        if (state.heartbeatTimer) window.clearInterval(state.heartbeatTimer);
        heartbeat();
        state.heartbeatTimer = window.setInterval(heartbeat, 10000);
    }

    async function pollResult(requestId) {
        const startedAt = Date.now();
        while (Date.now() - startedAt < 10000) {
            await new Promise((resolve) => window.setTimeout(resolve, 250));
            try {
                const result = await requestJson(
                    `/vlux/mobile/result?request_id=${encodeURIComponent(requestId)}`,
                    { method: "GET" },
                    true
                );
                if (result.status === "queued") continue;
                return result;
            } catch (error) {
                if (error.code === "REQUEST_NOT_FOUND") continue;
                throw error;
            }
        }
        throw new Error("La caja no confirmo la lectura a tiempo.");
    }

    async function sendBarcode(rawBarcode) {
        const barcode = String(rawBarcode || "").trim();
        if (!barcode || state.sending) return;

        const now = Date.now();
        if (barcode === state.lastCode && now - state.lastAcceptedAt < state.cooldownMs) return;

        state.sending = true;
        state.lastCode = barcode;
        setConnection("sending", "Enviando");
        setResult("sending", "Buscando producto...", barcode, "", "Enviando a Odoo POS.");

        try {
            const queued = await requestJson("/vlux/mobile/scan", {
                method: "POST",
                body: JSON.stringify({ barcode }),
            });
            const result = await pollResult(queued.request_id);

            if (result.status === "delivered") {
                state.lastAcceptedAt = Date.now();
                const productName = result.product?.name || "Producto";
                const price = result.product && Number.isFinite(Number(result.product.unit_price))
                    ? `$${Number(result.product.unit_price).toFixed(2)}` : "";
                setResult(
                    "success", "Producto enviado", barcode,
                    [productName, price].filter(Boolean).join(" · "),
                    "✓ Agregado correctamente al carrito."
                );
                feedback(true);
            } else if (result.status === "not_found") {
                setResult("error", "Producto no encontrado", barcode, "", result.message || "Odoo no encontro un producto para este codigo.");
                feedback(false);
            } else {
                setResult("error", "No fue posible agregar el producto", barcode, "", result.message || "La caja rechazo la lectura.");
                feedback(false);
            }
            setConnection("online", "Conectado");
        } catch (error) {
            if (error.code === "DUPLICATE_COOLDOWN") {
                setConnection("online", "Conectado");
                setResult("idle", "Lectura repetida ignorada", barcode, "", "Vuelve a enfocar el codigo despues del tiempo de seguridad.");
            } else {
                setConnection("reconnecting", "Reconectando");
                setResult("error", "No fue posible enviar el codigo", barcode, "", error.message || "Comprueba la conexion e intenta nuevamente.");
                feedback(false);
            }
        } finally {
            state.sending = false;
        }
    }

    function loadScriptOnce(src) {
        return new Promise((resolve, reject) => {
            const existing = document.querySelector(`script[data-vlux-src="${src}"]`);
            if (existing) {
                if (window.ZXing) resolve();
                else existing.addEventListener("load", resolve, { once: true });
                return;
            }
            const script = document.createElement("script");
            script.src = src;
            script.async = true;
            script.dataset.vluxSrc = src;
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }

    function buildZXingDetector(ZXing) {
        const formatMap = new Map([
            ["code_128", ZXing.BarcodeFormat.CODE_128],
            ["ean_8", ZXing.BarcodeFormat.EAN_8],
            ["ean_13", ZXing.BarcodeFormat.EAN_13],
            ["upc_a", ZXing.BarcodeFormat.UPC_A],
            ["upc_e", ZXing.BarcodeFormat.UPC_E],
        ]);
        return class ZXingDetector {
            constructor() {
                const hints = new Map([
                    [ZXing.DecodeHintType.POSSIBLE_FORMATS, Array.from(formatMap.values())],
                    [ZXing.DecodeHintType.TRY_HARDER, true],
                ]);
                this.reader = new ZXing.MultiFormatReader();
                this.reader.setHints(hints);
            }
            async detect(video) {
                if (!video.videoWidth || video.readyState < 2) return [];
                const canvas = document.createElement("canvas");
                canvas.width = video.videoWidth;
                canvas.height = video.videoHeight;
                const context = canvas.getContext("2d", { willReadFrequently: true });
                context.drawImage(video, 0, 0, canvas.width, canvas.height);
                const source = new ZXing.HTMLCanvasElementLuminanceSource(canvas);
                const bitmap = new ZXing.BinaryBitmap(new ZXing.HybridBinarizer(source));
                try {
                    const result = this.reader.decodeWithState(bitmap);
                    return [{ rawValue: result.getText() }];
                } catch (error) {
                    if (error.name === "NotFoundException") return [];
                    throw error;
                }
            }
        };
    }

    async function createDetector() {
        const desiredFormats = ["ean_13", "ean_8", "upc_a", "upc_e", "code_128"];
        if ("BarcodeDetector" in window) {
            const supported = await window.BarcodeDetector.getSupportedFormats();
            const formats = desiredFormats.filter((format) => supported.includes(format));
            if (formats.length) return new window.BarcodeDetector({ formats });
        }
        await loadScriptOnce("/web/static/lib/zxing-library/zxing-library.js");
        if (!window.ZXing) throw new Error("No fue posible cargar el lector ZXing local de Odoo.");
        const Detector = buildZXingDetector(window.ZXing);
        return new Detector();
    }

    async function detectionLoop() {
        if (!state.cameraRunning || !state.detector || !state.stream) return;
        if (!state.sending) {
            try {
                const codes = await state.detector.detect(el.cameraVideo);
                if (codes?.length && codes[0]?.rawValue) await sendBarcode(codes[0].rawValue);
            } catch (error) {
                console.debug("VLUX scanner frame error", error);
            }
        }
        if (state.cameraRunning) state.detectionTimer = window.setTimeout(detectionLoop, 120);
    }

    async function startCamera() {
        ensureAudioContext();
        if (!window.isSecureContext) {
            el.httpsWarning.classList.remove("is-hidden");
            setResult("error", "HTTPS requerido para la camara", "", "", "La prueba manual sigue disponible.");
            return;
        }
        if (!navigator.mediaDevices?.getUserMedia) {
            setResult("error", "Camara no compatible", "", "", "Este navegador no permite acceso a la camara.");
            return;
        }
        try {
            state.detector = await createDetector();
            state.stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: { ideal: "environment" } }, audio: false,
            });
            el.cameraVideo.srcObject = state.stream;
            await el.cameraVideo.play();
            state.cameraRunning = true;
            el.cameraPlaceholder.classList.add("is-hidden");
            el.cameraButton.classList.add("is-hidden");
            el.pauseButton.classList.remove("is-hidden");
            setResult("idle", "Camara activa", "", "", "Enfoca un codigo dentro del marco.");
            detectionLoop();
        } catch (error) {
            setResult("error", "No fue posible abrir la camara", "", "", error.message || "Revisa los permisos del navegador.");
        }
    }

    function stopCamera() {
        state.cameraRunning = false;
        if (state.detectionTimer) window.clearTimeout(state.detectionTimer);
        state.detectionTimer = null;
        if (state.stream) state.stream.getTracks().forEach((track) => track.stop());
        state.stream = null;
        if (el.cameraVideo) el.cameraVideo.srcObject = null;
        el.cameraPlaceholder?.classList.remove("is-hidden");
        el.cameraButton?.classList.remove("is-hidden");
        el.pauseButton?.classList.add("is-hidden");
    }

    async function disconnect() {
        stopCamera();
        if (state.heartbeatTimer) window.clearInterval(state.heartbeatTimer);
        if (state.token) {
            try {
                await requestJson("/vlux/mobile/disconnect", {
                    method: "POST",
                    body: JSON.stringify({}),
                });
            } catch {
                // El cierre local debe terminar aunque el servidor no responda.
            }
        }
        state.token = "";
        sessionStorage.removeItem(tokenKey);
        showPairing();
        setResult("idle", "Esperando un codigo");
    }

    el.pairButton.addEventListener("click", () => pair(el.pairCode.value));
    el.pairCode.addEventListener("keydown", (event) => { if (event.key === "Enter") pair(el.pairCode.value); });
    el.cameraButton.addEventListener("click", startCamera);
    el.pauseButton.addEventListener("click", stopCamera);
    el.manualSendButton.addEventListener("click", () => { ensureAudioContext(); sendBarcode(el.manualBarcode.value); el.manualBarcode.select(); });
    el.manualBarcode.addEventListener("keydown", (event) => { if (event.key === "Enter") { ensureAudioContext(); sendBarcode(el.manualBarcode.value); el.manualBarcode.select(); } });
    el.disconnectButton.addEventListener("click", () => disconnect());
    window.addEventListener("pagehide", stopCamera);

    if (!window.isSecureContext) el.httpsWarning.classList.remove("is-hidden");
    if (state.token) {
        heartbeat().then((valid) => {
            if (valid) {
                startHeartbeat();
            } else {
                sessionStorage.removeItem(tokenKey);
                state.token = "";
                if (initialPairCode) {
                    el.pairCode.value = initialPairCode;
                    pair(initialPairCode);
                } else {
                    showPairing();
                }
            }
        });
    } else if (initialPairCode) {
        el.pairCode.value = initialPairCode;
        pair(initialPairCode);
    } else {
        showPairing();
    }
})();
