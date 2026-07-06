import { supabase } from "@/lib/supabase/client";
import axios from "axios";

const apiBaseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const apiClient = axios.create({
    baseURL: `${apiBaseUrl}/api/v1`,
    headers: {
        "Content-Type": "application/json",
    },
});

// Attach the Supabase session's access_token as a Bearer header on every request.
// Falls through without auth header if the session cannot be retrieved.
apiClient.interceptors.request.use(async (config) => {
    try {
        const { data: { session } } = await supabase.auth.getSession();
        if (session?.access_token) {
            config.headers.Authorization = `Bearer ${session.access_token}`;
        }
    } catch {
        // Session unavailable — proceed without Authorization header.
        // The backend will return 401 if the endpoint requires auth.
    }
    return config;
});

export default apiClient;
