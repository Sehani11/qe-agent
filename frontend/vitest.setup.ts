import '@testing-library/jest-dom';
import { vi } from 'vitest';

// The Supabase browser client is constructed at module scope in
// `lib/supabase/client.ts`, which `lib/api/client.ts` imports. Anything that
// reaches the API client therefore needs these present just to IMPORT — the
// values are never used, because tests never let a real request out.
process.env.NEXT_PUBLIC_SUPABASE_URL ||= 'http://localhost:54321';
process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ||= 'test-anon-key';
process.env.NEXT_PUBLIC_API_URL ||= 'http://localhost:8000';

Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation(query => ({
        matches: false,
        media: query,
        onchange: null,
        addListener: vi.fn(), // Deprecated
        removeListener: vi.fn(), // Deprecated
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
    })),
});
