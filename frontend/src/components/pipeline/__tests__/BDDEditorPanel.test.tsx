import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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

describe('BDDEditorPanel Component', () => {
    const mockSetBddContent = vi.fn();

    beforeEach(() => {
        vi.clearAllMocks();
        (useSessionContext as any).mockReturnValue({
            sessionId: "test-session-123",
            bddContent: "Feature: Test Upload File",
            setBddContent: mockSetBddContent,
        });

        // Mock URL and Blob for downloads
        global.URL.createObjectURL = vi.fn(() => "blob:mock-url");
        global.URL.revokeObjectURL = vi.fn();
        HTMLAnchorElement.prototype.click = vi.fn();
    });

    it('renders the editor panel and control buttons', () => {
        render(<BDDEditorPanel />);

        expect(screen.getByText('BDD Scenario Editor')).toBeInTheDocument();
        expect(screen.getByText('Upload')).toBeInTheDocument();
        expect(screen.getByText('.feature')).toBeInTheDocument();
        expect(screen.getByText('CSV')).toBeInTheDocument();
        expect(screen.getByTestId('monaco-editor-mock')).toBeInTheDocument();
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
});
