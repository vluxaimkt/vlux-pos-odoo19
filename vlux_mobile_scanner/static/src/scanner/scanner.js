(() => {
    "use strict";

    const { ScanGate, PendingQueue, PollBackoff, retryDelayMs, newRequestId, cropPlan, classifySendError } =
        window.VluxScannerCore;

    const body = document.body;
    const dbName = body.dataset.db || "";
    const initialPairCode = (body.dataset.pairCode || "").trim().toUpperCase();
    const pushVersion = body.dataset.pushVersion || "";
    const tokenKey = `vlux_mobile_scanner_token_${dbName || "default"}`;

    const HEARTBEAT_MS = 30000;
    const PENDING_TTL_MS = 30000;
    const PUSH_SAFETY_NET_MS = 3000;
    const PUSH_SAFETY_POLL_MS = 2000;
    const DETECT_INTERVAL_NATIVE_MS = 90;
    const DETECT_INTERVAL_ZXING_MS = 140;
    const AFTER_ACCEPT_HOLD_MS = 200;
    const HISTORY_SIZE = 6;

    const el = Object.fromEntries([
        "pairPanel", "scannerPanel", "pairCode", "pairButton", "pairError",
        "connectionBadge", "connectionText", "posName", "cameraVideo",
        "cameraPlaceholder", "cameraButton", "pauseButton", "httpsWarning",
        "resultIcon", "resultTitle", "resultCode", "resultProduct", "resultMessage",
        "repeatButton", "queueBadge", "historyList", "transportBadge",
        "manualBarcode", "manualSendButton", "disconnectButton",
    ].map((id) => [id, document.getElementById(id)]));

    const state = {
        token: sessionStorage.getItem(tokenKey) || "",
        offlineFlushTimer: null,
        offlineFlushing: false,
        posName: "",
        pushChannel: "",
        stream: null,
        detector: null,
        detectorKind: "",
        detectionTimer: null,
        cameraRunning: false,
        heartbeatTimer: null,
        audioContext: null,
        lastBarcode: "",
        // transport
        socket: null,
        socketReady: false,
        socketRetryMs: 1000,
        socketTimer: null,
        pollTimer: null,
        history: [],
        // decoder scratch
        canvas: null,
        context: null,
    };
    const gate = new ScanGate({ gapMs: 400, minRepeatMs: 700 });
    // Scans survive a page reload: the queue lives next to the token.
    const queueKey = `${tokenKey}:queue`;
    const queue = PendingQueue.restore(sessionStorage.getItem(queueKey), { maxAttempts: 4 });
    const OFFLINE_FLUSH_MS = 5000;
    function persistQueue() {
        try {
            if (queue.size) sessionStorage.setItem(queueKey, queue.serialize());
            else sessionStorage.removeItem(queueKey);
        } catch { /* storage full or disabled: the in-memory queue still works */ }
    }
    const backoff = new PollBackoff({ minMs: 300, maxMs: 1500 });

    // ------------------------------------------------------------------
    // HTTP
    // ------------------------------------------------------------------

    function apiUrl(path) {
        return new URL(path, window.location.origin).toString();
    }

    async function requestJson(path, options = {}, auth = true) {
        const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
        if (auth && state.token) headers.Authorization = `Bearer ${state.token}`;
        // Odoo 19 resolves the database from the session cookie or this
        // header, never from ?db=. The header keeps the phone stateless.
        if (dbName) headers["X-Odoo-Database"] = dbName;
        const response = await fetch(apiUrl(path), { cache: "no-store", ...options, headers });
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

    // ------------------------------------------------------------------
    // UI helpers
    // ------------------------------------------------------------------

    function setConnection(mode, text) {
        el.connectionBadge.classList.remove("is-online", "is-sending", "is-reconnecting", "is-offline");
        el.connectionBadge.classList.add(`is-${mode}`);
        el.connectionText.textContent = text;
    }

    function setTransport(text) {
        if (el.transportBadge) el.transportBadge.textContent = text;
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
        closeSocket();
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
        if (el.repeatButton) el.repeatButton.classList.toggle("is-hidden", !state.lastBarcode);
    }

    function renderQueue() {
        persistQueue();
        if (!el.queueBadge) return;
        const size = queue.size;
        const offline = queue.offlineCount;
        el.queueBadge.textContent = offline
            ? `${offline} en espera · sin conexion`
            : size ? `${size} en proceso` : "";
        el.queueBadge.classList.toggle("is-hidden", !size);
        if (offline) setConnection("offline", "Sin conexion");
        else if (size) setConnection("sending", "Enviando");
        else if (state.token) setConnection("online", "Conectado");
    }

    function pushHistory(entry) {
        state.history.unshift(entry);
        state.history.length = Math.min(state.history.length, HISTORY_SIZE);
        if (!el.historyList) return;
        el.historyList.replaceChildren(
            ...state.history.map((row) => {
                const item = document.createElement("li");
                item.className = `vlux-history-item is-${row.type}`;
                const code = document.createElement("code");
                code.textContent = row.barcode;
                const text = document.createElement("span");
                text.textContent = row.text;
                item.append(code, text);
                return item;
            })
        );
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

    // ------------------------------------------------------------------
    // Pairing / heartbeat
    // ------------------------------------------------------------------

    function applyPairingInfo(data) {
        state.posName = data.pos_name;
        state.pushChannel = data.push_channel || "";
        showScanner(state.posName);
        if (state.pushChannel) openSocket();
    }

    async function pair(code) {
        const clean = (code || "").trim().toUpperCase();
        if (clean.length !== 8) {
            setPairError("Introduce un codigo de conexion de 8 caracteres.");
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
            sessionStorage.setItem(tokenKey, state.token);
            applyPairingInfo(data);
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
            const data = await requestJson("/vlux/mobile/heartbeat", { method: "POST", body: "{}" });
            applyPairingInfo(data);
            return true;
        } catch (error) {
            if (error.status === 401 || error.status === 409) {
                dropSession();
                return false;
            }
            setConnection("reconnecting", "Reconectando");
            return false;
        }
    }

    function startHeartbeat() {
        if (state.heartbeatTimer) window.clearInterval(state.heartbeatTimer);
        state.heartbeatTimer = window.setInterval(heartbeat, HEARTBEAT_MS);
    }

    function dropSession() {
        state.token = "";
        state.pushChannel = "";
        sessionStorage.removeItem(tokenKey);
        // Scans belong to the pairing that made them; a new register must not receive them.
        for (const id of queue.ids()) queue.resolve(id);
        persistQueue();
        if (state.heartbeatTimer) window.clearInterval(state.heartbeatTimer);
        showPairing();
        setResult("idle", "La vinculacion termino", "", "", "Vuelve a conectar el telefono con la caja.");
    }

    // ------------------------------------------------------------------
    // Results: push (Odoo bus websocket) with batch polling fallback
    // ------------------------------------------------------------------

    function socketUrl() {
        const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const url = new URL("/websocket", `${protocol}//${window.location.host}`);
        if (pushVersion) url.searchParams.set("version", pushVersion);
        return url.toString();
    }

    function openSocket() {
        if (!("WebSocket" in window) || !state.pushChannel || state.socket) return;
        let socket;
        try {
            socket = new WebSocket(socketUrl());
        } catch {
            scheduleSocketRetry();
            return;
        }
        state.socket = socket;
        socket.addEventListener("open", () => {
            state.socketReady = true;
            state.socketRetryMs = 1000;
            socket.send(JSON.stringify({
                event_name: "subscribe",
                data: { channels: [state.pushChannel], last: 0 },
            }));
            setTransport("push");
            schedulePoll(true);
        });
        socket.addEventListener("message", (event) => {
            let notifications;
            try { notifications = JSON.parse(event.data); } catch { return; }
            if (!Array.isArray(notifications)) return;
            for (const notification of notifications) {
                const message = notification?.message;
                if (message?.type !== "VLUX_MOBILE_RESULT") continue;
                for (const row of message.payload?.results || []) handleResult(row);
            }
        });
        const onGone = () => {
            if (state.socket !== socket) return;
            state.socket = null;
            state.socketReady = false;
            setTransport("polling");
            schedulePoll(true);
            if (state.token) scheduleSocketRetry();
        };
        socket.addEventListener("close", onGone);
        socket.addEventListener("error", onGone);
    }

    function scheduleSocketRetry() {
        if (state.socketTimer) return;
        state.socketTimer = window.setTimeout(() => {
            state.socketTimer = null;
            state.socketRetryMs = Math.min(30000, state.socketRetryMs * 2);
            openSocket();
        }, state.socketRetryMs);
    }

    function closeSocket() {
        if (state.socketTimer) window.clearTimeout(state.socketTimer);
        state.socketTimer = null;
        const socket = state.socket;
        state.socket = null;
        state.socketReady = false;
        try { socket?.close(); } catch { /* ignore */ }
    }

    function schedulePoll(immediate = false) {
        if (state.pollTimer) window.clearTimeout(state.pollTimer);
        state.pollTimer = null;
        if (!queue.size || !state.token) return;
        const delay = immediate ? Math.min(backoff.current, 100) : backoff.current;
        state.pollTimer = window.setTimeout(pollResults, state.socketReady ? PUSH_SAFETY_POLL_MS : delay);
    }

    async function pollResults() {
        state.pollTimer = null;
        if (!queue.size || !state.token) return;
        const now = Date.now();
        for (const item of queue.expire(now, PENDING_TTL_MS)) {
            finishScan(item, "error", "Sin respuesta de la caja", "La caja no confirmo la lectura a tiempo.");
        }
        // With a live push channel we only chase scans that look stuck. Scans
        // the server never accepted have nothing to poll for.
        const ids = state.socketReady ? queue.stale(now, PUSH_SAFETY_NET_MS) : queue.queuedIds();
        let gotResult = false;
        if (ids.length) {
            try {
                const data = await requestJson("/vlux/mobile/results", {
                    method: "POST",
                    body: JSON.stringify({ request_ids: ids.slice(0, 50) }),
                });
                for (const row of data.results || []) {
                    if (row.status !== "queued" && row.status !== "unknown") {
                        handleResult(row);
                        gotResult = true;
                    }
                }
            } catch (error) {
                if (error.status === 401) { dropSession(); return; }
            }
        }
        backoff.next(gotResult);
        schedulePoll();
    }

    function handleResult(row) {
        const item = queue.resolve(row.request_id, row);
        if (!item) return;
        if (row.status === "delivered") {
            const productName = row.product?.name || "Producto";
            const price = row.product && Number.isFinite(Number(row.product.unit_price))
                ? `$${Number(row.product.unit_price).toFixed(2)}` : "";
            finishScan(item, "success", "Producto enviado", "✓ Agregado al carrito.",
                [productName, price].filter(Boolean).join(" · "));
        } else if (row.status === "not_found") {
            const prompted = row.result_code === "REGISTER_PROMPTED";
            finishScan(item, "error", prompted ? "Producto sin registrar" : "Producto no encontrado",
                row.message || (prompted ? "La caja abrio el alta rapida." : "Odoo no encontro un producto para este codigo."));
        } else {
            finishScan(item, "error", "No fue posible agregar el producto", row.message || "La caja rechazo la lectura.");
        }
    }

    function finishScan(item, type, title, message, product = "") {
        setResult(type, title, item.barcode, product, message);
        pushHistory({ type, barcode: item.barcode, text: product || title });
        feedback(type === "success");
        renderQueue();
    }

    // ------------------------------------------------------------------
    // Sending scans (pipelined, idempotent, with retry)
    // ------------------------------------------------------------------

    async function sendScan(item) {
        const now = Date.now();
        queue.markSent(item.requestId, now);
        try {
            await requestJson("/vlux/mobile/scan", {
                method: "POST",
                body: JSON.stringify({ barcode: item.barcode, request_id: item.requestId }),
            });
            backoff.reset();
            renderQueue();
            schedulePoll(true);
        } catch (error) {
            const kind = classifySendError(error);
            if (kind === "session") {
                queue.resolve(item.requestId);
                dropSession();
                return;
            }
            if (kind === "closed") {
                queue.resolve(item.requestId);
                finishScan(item, "error", "Caja no disponible", error.message || "La caja no tiene sesion activa.");
                return;
            }
            if (kind === "rejected") {
                queue.resolve(item.requestId);
                finishScan(item, "error", "Lectura rechazada", error.message || "La caja rechazo el codigo.");
                return;
            }
            if (kind === "network") {
                // No connection: the scan waits, in order, until it is back.
                // The request_id makes the eventual resend idempotent.
                queue.markSendFailed(item.requestId, "network");
                setResult("sending", "Sin conexion", item.barcode, "", "La lectura se enviara al recuperar la red.");
                renderQueue();
                scheduleOfflineFlush();
                return;
            }
            setConnection("reconnecting", "Reconectando");
            if (queue.markSendFailed(item.requestId, "server")) {
                window.setTimeout(() => {
                    if (queue.get(item.requestId)) sendScan(item);
                }, retryDelayMs(item.attempts));
            } else {
                queue.resolve(item.requestId);
                finishScan(item, "error", "No fue posible enviar el codigo", "La caja no respondio; intenta nuevamente.");
            }
        }
    }

    /** Resend everything the server never accepted, oldest first, one at a time. */
    async function flushOffline() {
        state.offlineFlushTimer = null;
        if (!state.token || state.offlineFlushing) return;
        state.offlineFlushing = true;
        try {
            for (const id of queue.unsentIds()) {
                const item = queue.get(id);
                if (!item || item.status === "retry") continue;
                await sendScan(item);
                if (item.status === "offline") break; // still no network: stop and wait
            }
        } finally {
            state.offlineFlushing = false;
        }
        if (queue.offlineCount) scheduleOfflineFlush();
    }

    function scheduleOfflineFlush() {
        if (state.offlineFlushTimer) return;
        state.offlineFlushTimer = window.setTimeout(flushOffline, OFFLINE_FLUSH_MS);
    }

    function submitBarcode(rawBarcode) {
        const barcode = String(rawBarcode || "").trim();
        if (!barcode || !state.token) return;
        state.lastBarcode = barcode;
        const item = queue.add(newRequestId(), barcode, Date.now());
        setResult("sending", "Buscando producto...", barcode, "", "Enviando a Odoo POS.");
        renderQueue();
        sendScan(item);
    }

    // ------------------------------------------------------------------
    // Camera
    // ------------------------------------------------------------------

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
        const formats = [
            ZXing.BarcodeFormat.CODE_128, ZXing.BarcodeFormat.EAN_8, ZXing.BarcodeFormat.EAN_13,
            ZXing.BarcodeFormat.UPC_A, ZXing.BarcodeFormat.UPC_E,
        ];
        return class ZXingDetector {
            constructor() {
                const hints = new Map([
                    [ZXing.DecodeHintType.POSSIBLE_FORMATS, formats],
                    [ZXing.DecodeHintType.TRY_HARDER, true],
                ]);
                this.reader = new ZXing.MultiFormatReader();
                this.reader.setHints(hints);
            }
            async detect(video) {
                // Only the scan frame is decoded, downscaled to <= 640px wide, on a
                // canvas that is reused between frames instead of re-created.
                const plan = cropPlan(video.videoWidth, video.videoHeight);
                if (!plan || video.readyState < 2) return [];
                if (!state.canvas) {
                    state.canvas = document.createElement("canvas");
                    state.context = state.canvas.getContext("2d", { willReadFrequently: true });
                }
                if (state.canvas.width !== plan.dw || state.canvas.height !== plan.dh) {
                    state.canvas.width = plan.dw;
                    state.canvas.height = plan.dh;
                }
                state.context.drawImage(video, plan.sx, plan.sy, plan.sw, plan.sh, 0, 0, plan.dw, plan.dh);
                const source = new ZXing.HTMLCanvasElementLuminanceSource(state.canvas);
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
            try {
                const supported = await window.BarcodeDetector.getSupportedFormats();
                const formats = desiredFormats.filter((format) => supported.includes(format));
                if (formats.length) {
                    state.detectorKind = "native";
                    return new window.BarcodeDetector({ formats });
                }
            } catch { /* fall through to ZXing */ }
        }
        await loadScriptOnce("/web/static/lib/zxing-library/zxing-library.js");
        if (!window.ZXing) throw new Error("No fue posible cargar el lector ZXing local de Odoo.");
        state.detectorKind = "zxing";
        const Detector = buildZXingDetector(window.ZXing);
        return new Detector();
    }

    async function detectionLoop() {
        if (!state.cameraRunning || !state.detector || !state.stream) return;
        let delay = state.detectorKind === "native" ? DETECT_INTERVAL_NATIVE_MS : DETECT_INTERVAL_ZXING_MS;
        if (document.visibilityState === "hidden") {
            delay = 500; // nothing to see: keep the loop alive but idle
        } else {
            try {
                const codes = await state.detector.detect(el.cameraVideo);
                const now = Date.now();
                let accepted = false;
                for (const code of codes || []) {
                    if (code?.rawValue && gate.offer(code.rawValue, now)) {
                        submitBarcode(code.rawValue);
                        accepted = true;
                    }
                }
                if (accepted) delay += AFTER_ACCEPT_HOLD_MS;
            } catch (error) {
                console.debug("VLUX scanner frame error", error);
            }
        }
        if (state.cameraRunning) state.detectionTimer = window.setTimeout(detectionLoop, delay);
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
                video: {
                    facingMode: { ideal: "environment" },
                    width: { ideal: 1280 },
                    height: { ideal: 720 },
                },
                audio: false,
            });
            el.cameraVideo.srcObject = state.stream;
            await el.cameraVideo.play();
            state.cameraRunning = true;
            gate.reset();
            el.cameraPlaceholder.classList.add("is-hidden");
            el.cameraButton.classList.add("is-hidden");
            el.pauseButton.classList.remove("is-hidden");
            setResult("idle", "Camara activa", "", "", "Enfoca un codigo dentro del marco. Retira y vuelve a enfocar para repetir.");
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

    // ------------------------------------------------------------------
    // Disconnect / wiring
    // ------------------------------------------------------------------

    async function disconnect() {
        stopCamera();
        closeSocket();
        if (state.heartbeatTimer) window.clearInterval(state.heartbeatTimer);
        if (state.token) {
            try {
                await requestJson("/vlux/mobile/disconnect", { method: "POST", body: "{}" });
            } catch {
                // El cierre local debe terminar aunque el servidor no responda.
            }
        }
        state.token = "";
        state.pushChannel = "";
        sessionStorage.removeItem(tokenKey);
        for (const id of queue.ids()) queue.resolve(id);
        persistQueue();
        showPairing();
        setResult("idle", "Esperando un codigo");
    }

    function manualSend() {
        ensureAudioContext();
        submitBarcode(el.manualBarcode.value);
        el.manualBarcode.select();
    }

    el.pairButton.addEventListener("click", () => pair(el.pairCode.value));
    el.pairCode.addEventListener("keydown", (event) => { if (event.key === "Enter") pair(el.pairCode.value); });
    el.cameraButton.addEventListener("click", startCamera);
    el.pauseButton.addEventListener("click", stopCamera);
    el.manualSendButton.addEventListener("click", manualSend);
    el.manualBarcode.addEventListener("keydown", (event) => { if (event.key === "Enter") manualSend(); });
    el.repeatButton?.addEventListener("click", () => { ensureAudioContext(); submitBarcode(state.lastBarcode); });
    el.disconnectButton.addEventListener("click", () => disconnect());
    window.addEventListener("pagehide", stopCamera);
    window.addEventListener("online", () => {
        if (state.token) { heartbeat(); openSocket(); schedulePoll(true); flushOffline(); }
    });
    // After a reload, anything restored into the queue is resent right away.
    if (state.token && queue.unsentIds().length) flushOffline();
    document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible" && state.token) { openSocket(); schedulePoll(true); }
    });

    if (!window.isSecureContext) el.httpsWarning.classList.remove("is-hidden");
    if (state.token) {
        heartbeat().then((valid) => {
            if (valid) {
                startHeartbeat();
            } else if (!state.token) {
                if (initialPairCode) {
                    el.pairCode.value = initialPairCode;
                    pair(initialPairCode);
                } else {
                    showPairing();
                }
            } else {
                // Network problem with a stored token: keep it and retry on the heartbeat.
                showScanner(state.posName);
                startHeartbeat();
            }
        });
    } else if (initialPairCode) {
        el.pairCode.value = initialPairCode;
        pair(initialPairCode);
    } else {
        showPairing();
    }
})();
