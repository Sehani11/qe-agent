/**
 * Read the server's own explanation out of a failed request.
 *
 * The API wraps every error as `{ error, message, code }` (see the exception
 * handlers in `app/main.py`), and `message` is written to be shown to a person
 * — "Kaggle credentials are not configured on the server", "A training run is
 * already in progress". Discarding it and guessing in the client produces
 * exactly the failure this exists to prevent: a toast that lists the two
 * likely causes when the real one was a third.
 *
 * `fallback` covers the cases where there is genuinely nothing to read: the
 * request never reached the server, or it answered with a shape we do not know.
 */
export function apiErrorMessage(error: unknown, fallback: string): string {
    if (typeof error === "object" && error !== null && "response" in error) {
        const response = (error as { response?: { data?: { message?: unknown } } })
            .response;
        const message = response?.data?.message;
        if (typeof message === "string" && message.trim()) return message;
    }
    return fallback;
}
