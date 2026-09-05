/**
 * Browser file downloads for responses the API only serves to an authenticated
 * caller.
 *
 * A plain `<a href="/api/...">` cannot be used for any of these: the axios
 * client attaches the auth header, and a browser-initiated navigation does not
 * go through it. So the bytes are fetched as a Blob and handed to a synthetic
 * anchor instead.
 */

/** Trigger a browser download of a Blob under the given filename. */
export function triggerBlobDownload(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    // Released immediately: the click has already handed the blob to the
    // download manager, and holding the URL pins the bytes in memory for the
    // lifetime of the document.
    URL.revokeObjectURL(url);
}

/**
 * Read the filename from a Content-Disposition header, falling back.
 *
 * The server is the authority on what a file is called — an adapter zip is
 * named after the run that produced it — so the fallback is only for a
 * response that arrives without the header at all.
 */
export function filenameFromHeader(disposition: unknown, fallback: string): string {
    if (typeof disposition === "string") {
        const match = disposition.match(/filename="?([^"]+)"?/);
        if (match) return match[1];
    }
    return fallback;
}
