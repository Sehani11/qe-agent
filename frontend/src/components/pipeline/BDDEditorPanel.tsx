"use client";

import dynamic from "next/dynamic";
import React, { useRef } from "react";
import { useParams } from "next/navigation";
import { Monaco } from "@monaco-editor/react";
import { useSessionContext } from "@/context/SessionContext";
import { Download, Upload, FileText } from "lucide-react";
import { useBDDUpload } from "@/lib/hooks/useBDDUpload";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), {
    ssr: false,
    loading: () => (
        <div className="flex h-full min-h-[420px] items-center justify-center bg-sky-50/60 p-6 text-sm text-slate-500">
            Loading editor...
        </div>
    ),
});

export default function BDDEditorPanel() {
    const {
        sessionId,
        setSessionId,
        bddContent,
        setBddContent,
        bddScenarios,
        setJiraTicketId,
    } = useSessionContext();
    const params = useParams();
    const routeSessionId = params.sessionId as string | undefined;
    const editorRef = useRef<unknown>(null);
    const fileInputRef = useRef<HTMLInputElement>(null);
    const [uploadError, setUploadError] = React.useState<string | null>(null);
    const [isUploading, setIsUploading] = React.useState(false);
    const [isMobile, setIsMobile] = React.useState(false);
    const uploadBDD = useBDDUpload();

    React.useEffect(() => {
        const check = () => setIsMobile(window.innerWidth < 768);
        check();
        window.addEventListener("resize", check);
        return () => window.removeEventListener("resize", check);
    }, []);

    // Monaco doesn't reliably re-render from external `value` prop changes —
    // its internal model holds the previous content. Push bddContent into the
    // editor whenever they diverge so context resets (e.g. starting a new
    // session) actually clear the visible scenarios.
    React.useEffect(() => {
        const editor = editorRef.current as
            | { getValue: () => string; setValue: (v: string) => void }
            | null;
        if (!editor) return;
        if (editor.getValue() !== bddContent) {
            editor.setValue(bddContent);
        }
    }, [bddContent]);

    const handleEditorChange = (value: string | undefined) => {
        if (value !== undefined) {
            setBddContent(value);
        }
    };

    const handleEditorBeforeMount = (monaco: Monaco) => {
        registerGherkinLanguage(monaco);
    };

    const handleEditorDidMount = (editor: unknown) => {
        editorRef.current = editor;
    };

    const downloadBlob = (content: string, filename: string, mimeType: string) => {
        const blob = new Blob([content], { type: mimeType });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const handleDownloadFeature = () => {
        const filename = `${sessionId || "bdd"}.feature`;
        downloadBlob(bddContent, filename, "text/plain;charset=utf-8");
    };

    const handleDownloadCSV = () => {
        const escapeCSV = (str: string) => `"${(str ?? "").replace(/"/g, '""')}"`;

        if (bddScenarios.length > 0) {
            // Use structured scenario data — accurate, no text parsing needed
            const csvHeader = "Feature,Scenario,Given,When,Then,Expected Result\n";
            const rows = bddScenarios.map((s) =>
                [s.feature, s.scenario, s.given, s.when, s.then, ""].map(escapeCSV).join(",")
            );
            downloadBlob(csvHeader + rows.join("\n"), `${sessionId || "bdd"}.csv`, "text/csv;charset=utf-8");
        } else {
            // Fallback: parse Gherkin text (for manually edited or uploaded content)
            const lines = bddContent.split("\n");
            const rows: string[][] = [];
            let currentFeature = "";
            let currentScenario = "";
            let currentGiven = "";
            let currentWhen = "";
            let currentThen = "";
            let lastKeyword: "given" | "when" | "then" | null = null;

            const pushRow = () => {
                if (currentScenario) {
                    rows.push([currentFeature, currentScenario, currentGiven, currentWhen, currentThen, ""]);
                }
            };

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed || trimmed.startsWith("#")) continue;

                if (trimmed.startsWith("Feature:")) {
                    currentFeature = trimmed.replace(/Feature:\s*/, "");
                } else if (trimmed.startsWith("Scenario Outline:") || trimmed.startsWith("Scenario:")) {
                    pushRow();
                    currentScenario = trimmed.replace(/Scenario( Outline)?:\s*/, "");
                    currentGiven = ""; currentWhen = ""; currentThen = "";
                    lastKeyword = null;
                } else if (trimmed.startsWith("Given ")) {
                    currentGiven += (currentGiven ? "\n" : "") + trimmed;
                    lastKeyword = "given";
                } else if (trimmed.startsWith("When ")) {
                    currentWhen += (currentWhen ? "\n" : "") + trimmed;
                    lastKeyword = "when";
                } else if (trimmed.startsWith("Then ")) {
                    currentThen += (currentThen ? "\n" : "") + trimmed;
                    lastKeyword = "then";
                } else if (trimmed.startsWith("And ") || trimmed.startsWith("But ")) {
                    if (lastKeyword === "then") currentThen += "\n" + trimmed;
                    else if (lastKeyword === "when") currentWhen += "\n" + trimmed;
                    else if (lastKeyword === "given") currentGiven += "\n" + trimmed;
                }
            }
            pushRow();

            const csvHeader = "Feature,Scenario,Given,When,Then,Expected Result\n";
            downloadBlob(csvHeader + rows.map(r => r.map(escapeCSV).join(",")).join("\n"), `${sessionId || "bdd"}.csv`, "text/csv;charset=utf-8");
        }
    };

    const handleUploadFeature = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file) return;

        // Reset the file input regardless of outcome so re-selecting the same
        // file still triggers onChange.
        const resetInput = () => {
            if (fileInputRef.current) fileInputRef.current.value = "";
        };

        try {
            const text = await file.text();

            // Set on Monaco editor instance directly for immediate display —
            // Monaco doesn't reliably react to external value prop changes
            if (editorRef.current) {
                (editorRef.current as { setValue: (v: string) => void }).setValue(text);
            }
            setBddContent(text);
            setUploadError(null);

            // Fall back to the route's session UUID if the user hasn't
            // ingested a Jira ticket yet — the backend will create the
            // session row on demand with a filename-derived placeholder.
            const targetSessionId = sessionId ?? routeSessionId;
            if (!targetSessionId) {
                setUploadError(
                    "File loaded locally, but no session URL is available to save it to."
                );
                return;
            }

            setIsUploading(true);
            const response = await uploadBDD.mutateAsync({
                session_id: targetSessionId,
                file,
            });
            // Sync context to the (possibly newly-created) session so
            // subsequent operations (verification, generation) know about it.
            setSessionId(response.session_id);
            setJiraTicketId(response.jira_ticket_id);
        } catch (err) {
            const detail =
                typeof err === "object" && err !== null && "response" in err
                    ? (err as { response?: { data?: { detail?: string; message?: string } } })
                          .response?.data?.detail ??
                      (err as { response?: { data?: { detail?: string; message?: string } } })
                          .response?.data?.message
                    : err instanceof Error
                      ? err.message
                      : null;
            setUploadError(
                `File loaded locally, but saving to the server failed${detail ? `: ${detail}` : "."}`
            );
        } finally {
            setIsUploading(false);
            resetInput();
        }
    };

    return (
        <div className="flex flex-col overflow-hidden rounded-3xl border border-sky-100 bg-white shadow-[0_20px_70px_-30px_rgba(37,99,235,0.16)]">
            <div className="flex flex-col border-b border-slate-200 bg-white">
                <div className="flex items-center justify-between px-5 py-4">
                    <div className="flex items-center space-x-3">
                        <div>
                            <h2 className="text-base font-semibold tracking-wide text-slate-900">BDD Scenario Editor</h2>
                            <p className="text-sm text-slate-500">Review, refine, and download generated Gherkin scenarios.</p>
                        </div>
                    </div>

                    <div className="flex items-center space-x-2">
                        <input
                            type="file"
                            accept=".feature"
                            ref={fileInputRef}
                            onChange={handleUploadFeature}
                            className="hidden"
                        />
                        <button
                            onClick={() => fileInputRef.current?.click()}
                            className="flex items-center space-x-1.5 rounded-xl border border-sky-200 bg-sky-50 px-3 py-2 text-xs font-semibold text-sky-800 transition-colors hover:bg-sky-100"
                        >
                            <Upload className="w-3.5 h-3.5" />
                            <span>Upload</span>
                        </button>

                        <button
                            onClick={handleDownloadFeature}
                            className="flex items-center space-x-1.5 rounded-xl border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-800 transition-colors hover:bg-blue-100"
                            disabled={!bddContent}
                        >
                            <FileText className="w-3.5 h-3.5" />
                            <span>.feature</span>
                        </button>

                        <button
                            onClick={handleDownloadCSV}
                            className="flex items-center space-x-1.5 rounded-xl border border-indigo-200 bg-indigo-50 px-3 py-2 text-xs font-semibold text-indigo-800 transition-colors hover:bg-indigo-100 disabled:opacity-50"
                            disabled={!bddContent || isUploading}
                        >
                            <Download className="w-3.5 h-3.5" />
                            <span>CSV</span>
                        </button>
                    </div>
                </div>

                {uploadError && (
                    <div className="test-upload-error flex items-center justify-between border-b border-rose-200 bg-rose-50 px-5 py-3 text-xs font-medium text-rose-700">
                        <span>{uploadError}</span>
                        <button onClick={() => setUploadError(null)} className="hover:text-rose-900">
                            Dismiss
                        </button>
                    </div>
                )}
            </div>

            {/* Mobile: plain textarea — Monaco is not touch-friendly */}
            {isMobile ? (
                <textarea
                    value={bddContent}
                    onChange={(e) => setBddContent(e.target.value)}
                    placeholder="BDD scenarios will appear here after generation…"
                    spellCheck={false}
                    className="w-full resize-none bg-sky-50 p-4 font-mono text-sm text-slate-900 outline-none focus:bg-white"
                    style={{ minHeight: "320px" }}
                />
            ) : (
                <MonacoEditor
                    // Unique path per session → unique Monaco model URI.
                    // Without this, every BDDEditorPanel instance shares the
                    // library's default-URI model, so the previous session's
                    // content survives navigation to a new session.
                    path={`session-${routeSessionId ?? sessionId ?? "anon"}.feature`}
                    height="calc(100vh - 224px)"
                    defaultLanguage="gherkin"
                    theme="gherkin-light"
                    value={bddContent}
                    beforeMount={handleEditorBeforeMount}
                    onChange={handleEditorChange}
                    onMount={handleEditorDidMount}
                    options={{
                        minimap: { enabled: false },
                        fontSize: 14,
                        wordWrap: "on",
                        scrollBeyondLastLine: false,
                        padding: { top: 16, bottom: 16 },
                        readOnly: false,
                        lineNumbersMinChars: 3,
                        folding: true,
                    }}
                />
            )}
        </div>
    );
}

// Custom Gherkin Syntax Registration for Monaco
function registerGherkinLanguage(monaco: Monaco) {
    monaco.languages.register({ id: "gherkin" });

    monaco.languages.setMonarchTokensProvider("gherkin", {
        defaultToken: "",
        keywords: [
            "Feature", "Background", "Scenario", "Scenario Outline", "Scenario Template",
            "Examples", "Scenarios", "Given", "When", "Then", "And", "But", "Rule"
        ],
        tokenizer: {
            root: [
                [/^\s*(Feature|Background|Scenario( Outline| Template)?|Rule|Examples|Scenarios)(:)/, ["keyword", "keyword"]],
                [/^\s*(Given|When|Then|And|But)\b/, "type"],
                [/#.*$/, "comment"],
                [/@\w+/, "annotation"],
                [/".*?"/, "string"],
                [/'.*?'/, "string"],
                [/<.*?>/, "variable"],
                [/\|/, "delimiter"],
            ],
        },
    });

    monaco.editor.defineTheme("gherkin-light", {
        base: "vs",
        inherit: true,
        colors: {
            "editor.background": "#eff6ff",
            "editor.lineHighlightBackground": "#dbeafe",
            "editorLineNumber.foreground": "#94a3b8",
            "editorCursor.foreground": "#0f172a",
        },
        rules: [
            { token: "keyword", foreground: "2563eb", fontStyle: "bold" },
            { token: "type", foreground: "1d4ed8", fontStyle: "bold" },
            { token: "comment", foreground: "64748b" },
            { token: "annotation", foreground: "4f46e5" },
            { token: "string", foreground: "1e40af" },
            { token: "variable", foreground: "2563eb" },
            { token: "delimiter", foreground: "94a3b8" },
        ],
    });
}
