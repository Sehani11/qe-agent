import { createBrowserClient } from "@supabase/ssr";

// Browser-side Supabase client — uses cookies via @supabase/ssr so session is
// shared with the server-side client and auth interceptors work correctly.
export const supabase = createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
);
