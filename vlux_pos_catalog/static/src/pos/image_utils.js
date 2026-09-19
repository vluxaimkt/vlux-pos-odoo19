/**
 * Client-side image preparation for the quick product form.
 *
 * Photos taken with a phone camera are typically 3-12 MB. The product card in
 * the POS only ever shows image_128, and Odoo derives every size from
 * image_1920, so uploading more than ~1024px is wasted filestore and network.
 */
export const MAX_DIMENSION = 1024;
export const JPEG_QUALITY = 0.85;
export const MAX_SOURCE_BYTES = 25 * 1024 * 1024;

/**
 * Compute the target size that fits inside a square of `maxDimension` while
 * keeping the aspect ratio. Pure, so it can be unit tested without a DOM.
 */
export function fitDimensions(width, height, maxDimension = MAX_DIMENSION) {
    if (!width || !height) {
        return { width: 0, height: 0 };
    }
    const scale = Math.min(1, maxDimension / Math.max(width, height));
    return {
        width: Math.max(1, Math.round(width * scale)),
        height: Math.max(1, Math.round(height * scale)),
    };
}

function loadImage(file) {
    return new Promise((resolve, reject) => {
        const url = URL.createObjectURL(file);
        const image = new Image();
        image.onload = () => {
            URL.revokeObjectURL(url);
            resolve(image);
        };
        image.onerror = () => {
            URL.revokeObjectURL(url);
            reject(new Error("unreadable image"));
        };
        image.src = url;
    });
}

/**
 * Downscale a File/Blob to a JPEG data URL bounded by MAX_DIMENSION.
 * Returns `{ dataUrl, base64, width, height, bytes }`.
 */
export async function prepareProductImage(file, options = {}) {
    if (!file || !String(file.type || "").startsWith("image/")) {
        throw new Error("not an image");
    }
    if (file.size > MAX_SOURCE_BYTES) {
        throw new Error("image too large");
    }
    const maxDimension = options.maxDimension || MAX_DIMENSION;
    const quality = options.quality || JPEG_QUALITY;
    const image = await loadImage(file);
    const { width, height } = fitDimensions(image.naturalWidth, image.naturalHeight, maxDimension);
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, width, height);
    context.drawImage(image, 0, 0, width, height);
    const dataUrl = canvas.toDataURL("image/jpeg", quality);
    const base64 = dataUrl.split(",", 2)[1] || "";
    return { dataUrl, base64, width, height, bytes: Math.floor((base64.length * 3) / 4) };
}
