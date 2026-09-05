import { render, screen, fireEvent, waitFor } from '@/test/test-utils';
import '@testing-library/jest-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import BDDEditorPanel from '../BDDEditorPanel';
import { useSessionContext } from '@/context/SessionContext';

// --- Mocks ---

vi.mock('@/context/SessionContext', () => ({
    useSessionContext: vi.fn(),
}));

vi.mock('next/navigation', () => ({
    useParams: () => ({ sessionId: 'test-session-123' }),
}));

vi.mock('@monaco-editor/react', () => {
    const EditorMock = () => <div data-testid="monaco-editor-mock">Mock Editor</div>;
    return {
        __esModule: true,
        default: EditorMock,
        Editor: EditorMock,
        useMonaco: () => ({
            languages: {
                register: vi.fn(),
                setMonarchTokensProvider: vi.fn(),
            },
            editor: {
                defineTheme: vi.fn(),
            }
        }),
    };
});

const mockUploadMutateAsync = vi.fn();
vi.mock('@/lib/hooks/useBDDUpload', () => ({
    useBDDUpload: () => ({
        mutateAsync: mockUploadMutateAsync,
    }),
}));

const mockSaveMutateAsync = vi.fn();
vi.mock('@/lib/hooks/useBDDSave', () => ({
    useBDDSave: () => ({
        mutateAsync: mockSaveMutateAsync,
    }),
}));

// The context mock returns only the slice of SessionContext this panel reads,
// so it is narrowed through a mock type rather than `any` (Mandatory Rule 8).
const mockUseSessionContext = vi.mocked(useSessionContext) as unknown as {
    mockReturnValue: (value: Record<string, unknown>) => void;
};

describe('BDDEditorPanel Component', () => {
    const mockSetBddContent = vi.fn();

    beforeEach(() => {
        vi.clearAllMocks();
        mockUseSessionContext.mockReturnValue({
            sessionId: "test-session-123",
            bddContent: "Feature: Test Upload File",
            setBddContent: mockSetBddContent,
        });

        // Mock URL and Blob for downloads
        global.URL.createObjectURL = vi.fn(() => "blob:mock-url");
        global.URL.revokeObjectURL = vi.fn();
        HTMLAnchorElement.prototype.click = vi.fn();
    });

    it('renders the editor panel and control buttons', async () => {
        render(<BDDEditorPanel />);

        expect(screen.getByText('BDD Scenario Editor')).toBeInTheDocument();
        expect(screen.getByText('Upload')).toBeInTheDocument();
        expect(screen.getByText('.feature')).toBeInTheDocument();
        expect(screen.getByText('CSV')).toBeInTheDocument();
        // The panel pulls Monaco in through next/dynamic, so the mock only
        // mounts once that import resolves — the loading placeholder is what
        // renders synchronously.
        expect(await screen.findByTestId('monaco-editor-mock')).toBeInTheDocument();
    });

    it('handles download .feature button click', () => {
        render(<BDDEditorPanel />);

        const downloadBtn = screen.getByText('.feature').parentElement!;
        fireEvent.click(downloadBtn);

        expect(global.URL.createObjectURL).toHaveBeenCalled();
        expect(HTMLAnchorElement.prototype.click).toHaveBeenCalled();
    });

    it('loads file into editor and surfaces backend error if upload fails', async () => {
        mockUploadMutateAsync.mockRejectedValueOnce({
            response: { data: { detail: "Session not found." } },
        });
        render(<BDDEditorPanel />);

        const fileInput = document.querySelector('input[type="file"]') as HTMLInputElement;
        expect(fileInput).toBeInTheDocument();

        const testFile = new File(['Feature: New uploaded features'], 'upload.feature', { type: 'text/plain' });

        fireEvent.change(fileInput, { target: { files: [testFile] } });

        await waitFor(() => {
            expect(screen.getByText(/saving to the server failed.*Session not found/i)).toBeInTheDocument();
        });

        // Local editor content is populated even when the server upload fails
        expect(mockSetBddContent).toHaveBeenCalledWith('Feature: New uploaded features');
        expect(mockUploadMutateAsync).toHaveBeenCalledWith({
            session_id: 'test-session-123',
            file: testFile,
        });
    });

    // --- Story 6.4: capturing human corrections ---

    describe('Save (training-data capture)', () => {
        // Monaco is mocked and exposes no editable surface, so these drive the
        // mobile textarea — the same handleEditorChange path the editor uses.
        beforeEach(() => {
            window.innerWidth = 500;
        });

        // Queried by aria-label rather than placeholder copy, which drifts.
        const editContent = (value: string) => {
            fireEvent.change(screen.getByLabelText('BDD scenarios'), {
                target: { value },
            });
        };

        it('disables Save for untouched generated content', () => {
            // The editor is populated externally after generation and on session
            // load. Saving that as an "edit" would write a correction identical
            // to its parent - poisoning the very dataset this story builds.
            render(<BDDEditorPanel />);

            const saveBtn = screen.getByTestId('save-bdd-button');
            expect(saveBtn).toBeInTheDocument();
            expect(saveBtn).toBeDisabled();
        });

        it('enables Save once the user actually edits the content', () => {
            render(<BDDEditorPanel />);

            editContent('Feature: Test Upload File\n  Scenario: Human added');

            expect(screen.getByTestId('save-bdd-button')).not.toBeDisabled();
        });

        it('disables Save when the editor is empty', () => {
            mockUseSessionContext.mockReturnValue({
                sessionId: 'test-session-123',
                bddContent: '',
                setBddContent: mockSetBddContent,
            });
            render(<BDDEditorPanel />);

            expect(screen.getByTestId('save-bdd-button')).toBeDisabled();
        });

        it('posts the current editor content for the session', async () => {
            mockSaveMutateAsync.mockResolvedValueOnce({
                id: 'row-1',
                session_id: 'test-session-123',
                source: 'edited',
                created_at: '2026-08-08T00:00:00Z',
            });
            render(<BDDEditorPanel />);

            editContent('Feature: Edited by a human');
            fireEvent.click(screen.getByTestId('save-bdd-button'));

            await waitFor(() => {
                // The context mock does not re-render with new content, so the
                // posted value is the mocked bddContent - what matters here is
                // that the CURRENT editor content is what gets sent.
                expect(mockSaveMutateAsync).toHaveBeenCalledWith({
                    session_id: 'test-session-123',
                    content: 'Feature: Test Upload File',
                });
            });
        });

        it('marks content clean after a successful save', async () => {
            mockSaveMutateAsync.mockResolvedValueOnce({
                id: 'row-1',
                session_id: 'test-session-123',
                source: 'edited',
                created_at: '2026-08-08T00:00:00Z',
            });
            render(<BDDEditorPanel />);

            editContent('Feature: Edited by a human');
            fireEvent.click(screen.getByTestId('save-bdd-button'));

            // Unchanged content must not be re-saved as a duplicate correction
            await waitFor(() => {
                expect(screen.getByTestId('save-bdd-button')).toBeDisabled();
            });
            expect(screen.getByText('Saved')).toBeInTheDocument();
        });

        it('surfaces an error and stays dirty when saving fails', async () => {
            mockSaveMutateAsync.mockRejectedValueOnce({
                response: { data: { detail: 'Session not found.' } },
            });
            render(<BDDEditorPanel />);

            editContent('Feature: Edited by a human');
            fireEvent.click(screen.getByTestId('save-bdd-button'));

            await waitFor(() => {
                expect(
                    screen.getByText(/Saving your edits failed.*Session not found/i)
                ).toBeInTheDocument();
            });
            // A failed save must not be mistaken for a persisted one
            expect(screen.getByTestId('save-bdd-button')).not.toBeDisabled();
        });
    });
});
