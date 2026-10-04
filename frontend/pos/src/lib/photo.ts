/**
 * A product picture ready to upload: downsized to ~1024 px JPEG on the device
 * (Odoo derives every size from it, so more is wasted network and storage).
 */
export const PHOTO_MAX_SIDE = 1024;
const QUALITY = 0.85;

export function downsizeImage(file: Blob, maxSide = PHOTO_MAX_SIDE): Promise<string> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      const scale = Math.min(1, maxSide / Math.max(image.naturalWidth, image.naturalHeight));
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
      canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
      const context = canvas.getContext("2d");
      URL.revokeObjectURL(url);
      if (!context) return reject(new Error("Este equipo no puede preparar la foto."));
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      resolve(canvas.toDataURL("image/jpeg", QUALITY));
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("No se pudo leer la foto."));
    };
    image.src = url;
  });
}
