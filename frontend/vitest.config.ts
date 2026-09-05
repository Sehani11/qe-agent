import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
    plugins: [react()],
    test: {
        globals: true,
        environment: 'jsdom',
        // Well above what any test needs to DO — it buys headroom for how long
        // jsdom takes to ANSWER. `getByRole` recomputes the accessibility tree
        // per query, so the sessions-pagination tests (a 20-row table plus the
        // app nav, queried repeatedly) already ran within a second of the 5s
        // default and one of them timed out intermittently. Adding a single
        // control to the nav was enough to push three over. Raise this only
        // with that in mind: a test that newly needs it is usually querying too
        // often, not doing too much.
        testTimeout: 20000,
        setupFiles: ['./vitest.setup.ts'],
        alias: {
            '@': path.resolve(__dirname, './src')
        }
    }
})
