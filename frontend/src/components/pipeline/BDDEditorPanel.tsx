"use client";

import dynamic from "next/dynamic";
import React, { useRef } from "react";
import { useParams } from "next/navigation";
import { Monaco } from "@monaco-editor/react";
import { useSessionContext } from "@/context/SessionContext";
import { Check, Download, FileText, Save, Upload } from "lucide-react";
import { useBDDUpload } from "@/lib/hooks/useBDDUpload";
import { useBDDSave } from "@/lib/hooks/useBDDSave";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { InlineLoader } from "@/components/ui/loaders";
import { useToast } from "@/components/ui/toast";
import { useTheme } from "@/providers/ThemeProvider";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), {
    ssr: false,
    loading: () => (
        <div className="flex h-full min-h-[420px] items-center justify-center bg-surface-raised p-6">
            <InlineLoader>Loading editor</InlineLoader>
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
    const [isConfirmingUpload, setIsConfirmingUpload] = React.useState(false);
    const uploadBDD = useBDDUpload();
    const saveBDD = useBDDSave();
    const toast = useToast();
    const { resolved: themeMode } = useTheme();

    // Story 6.4 — a human correcting generated BDD is the single most valuable
    // training signal, and it is lost the moment the page unloads.
    //
    // Dirtiness is tracked from ACTUAL keystrokes, not by comparing against a
    // baseline. `bddContent` is populated externally on generation and on
    // session load, so a content-comparison approach would report "dirty"
    // immediately and let the user save untouched model output as a human
    // correction — poisoning the dataset with rows whose "correction" equals
    // their parent. Only edits made in this editor count.
    const [hasUserEdited, setHasUserEdited] = React.useState(false);
    const [isSaving, setIsSaving] = React.useState(false);
    const [justSaved, setJustSaved] = React.useState(false);

    const isDirty = bddContent.trim().length > 0 && hasUserEdited;

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

    const openFilePicker = () => {
        setIsConfirmingUpload(false);
        fileInputRef.current?.click();
    };

    // Uploading replaces whatever is in the editor, and an unsaved human edit
    // is not recoverable — so the picker only opens unprompted when there is
    // nothing to overwrite.
    const handleUploadClick = () => {
        if (bddContent.trim().length > 0) {
            setIsConfirmingUpload(true);
            return;
        }
        openFilePicker();
    };

    const handleEditorChange = (value: string | undefined) => {
        if (value !== undefined) {
            // Only a keystroke in the editor marks content as a human edit.
            if (value !== bddContent) {
                setHasUserEdited(true);
                setJustSaved(false);
            }
            setBddContent(value);
        }
    };

    const handleEditorBeforeMount = (monaco: Monaco) => {
        registerGherkinLanguage(monaco);
    };

    const handleEditorDidMount = (editor: unknown) => {
        editorRef.current = editor;
    };

    const handleSave = async () => {
        const targetSessionId = sessionId ?? routeSessionId;
        if (!targetSessionId) {
            setUploadError("No session URL is available to save this edit to.");
            return;
        }

        setIsSaving(true);
        try {
            await saveBDD.mutateAsync({
                session_id: targetSessionId,
                content: bddContent,
            });
            // Persisted — further saves need a fresh edit, so an unchanged
            // document cannot be recorded twice as a correction.
            setHasUserEdited(false);
            setUploadError(null);
            setJustSaved(true);
            toast.success("Scenarios saved", {
                description: "Your edits are recorded against this session.",
            });
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
            const message = `Saving your edits failed${detail ? `: ${detail}` : "."}`;
            setUploadError(message);
            toast.error("Save failed", {
                description: detail ?? "The server rejected the edit. Try again.",
            });
        } finally {
            setIsSaving(false);
        }
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
        toast.success("Feature file exported", { description: filename });
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

        toast.success("CSV exported", { description: `${sessionId || "bdd"}.csv` });
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
            // The upload itself persisted this text, so it is not an edit.
            setHasUserEdited(false);
            setJustSaved(false);
            toast.success("Feature file loaded", { description: file.name });
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
            toast.error("Upload not saved", {
                description:
                    "The file is open in the editor but the server did not store it. Save again once you are back online.",
            });
        } finally {
            setIsUploading(false);
            resetInput();
        }
    };

    return (
        <div className="flex flex-col overflow-hidden rounded-lg border border-rule bg-card">
            <div className="flex flex-col border-b border-rule">
                <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-3.5">
                    <div className="min-w-0">
                        <p className="eyebrow text-muted-foreground">Feature</p>
                        <h2 className="mt-1 text-base">BDD Scenario Editor</h2>
                        <p className="mt-1 text-[0.8125rem] text-muted-foreground">
                            Edit the generated Gherkin, then export it or verify it against code.
                        </p>
                    </div>

                    <div className="flex flex-wrap items-center gap-1.5">
                        <input
                            type="file"
                            accept=".feature"
                            ref={fileInputRef}
                            onChange={handleUploadFeature}
                            className="hidden"
                        />
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleUploadClick}
                            loading={isUploading}
                        >
                            {!isUploading && <Upload className="h-3.5 w-3.5" aria-hidden="true" />}
                            <span>Upload</span>
                        </Button>

                        <Button
                            variant={justSaved ? "outline" : "default"}
                            size="sm"
                            onClick={handleSave}
                            data-testid="save-bdd-button"
                            loading={isSaving}
                            disabled={!isDirty || isSaving}
                        >
                            {!isSaving &&
                                (justSaved ? (
                                    <Check className="h-3.5 w-3.5 text-pass" aria-hidden="true" />
                                ) : (
                                    <Save className="h-3.5 w-3.5" aria-hidden="true" />
                                ))}
                            <span>{isSaving ? "Saving…" : justSaved ? "Saved" : "Save"}</span>
                        </Button>

                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleDownloadFeature}
                            disabled={!bddContent}
                        >
                            <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                            <span>.feature</span>
                        </Button>

                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleDownloadCSV}
                            disabled={!bddContent || isUploading}
                        >
                            <Download className="h-3.5 w-3.5" aria-hidden="true" />
                            <span>CSV</span>
                        </Button>
                    </div>
                </div>

                {uploadError && (
                    <div
                        className="test-upload-error gutter-rule flex items-center justify-between gap-3 border-t border-rule bg-fail-soft/60 px-4 py-2.5"
                        data-signal="fail"
                        role="alert"
                    >
                        <span className="text-xs text-foreground">{uploadError}</span>
                        <button
                            onClick={() => setUploadError(null)}
                            className="shrink-0 font-mono text-xs font-semibold text-muted-foreground transition-colors hover:text-foreground"
                        >
                            Dismiss
                        </button>
                    </div>
                )}
            </div>

            {/* Mobile: plain textarea — Monaco is not touch-friendly */}
            {isMobile ? (
                <textarea
                    value={bddContent}
                    onChange={(e) => handleEditorChange(e.target.value)}
                    placeholder="Scenarios appear here once you generate or upload them…"
                    spellCheck={false}
                    aria-label="BDD scenarios"
                    className="w-full resize-none bg-surface-raised p-4 font-mono text-sm text-foreground outline-none placeholder:text-muted-foreground/70 focus:bg-card"
                    style={{ minHeight: "320px" }}
                />
            ) : (
                <MonacoEditor
                    // Unique path per session → unique Monaco model URI.
                    // Without this, every BDDEditorPanel instance shares the
                    // library's default-URI model, so the previous session's
                    // content survives navigation to a new session.
                    path={`session-${routeSessionId ?? sessionId ?? "anon"}.feature`}
                    height="calc(100vh - 260px)"
                    defaultLanguage="gherkin"
                    theme={themeMode === "dark" ? "gherkin-dark" : "gherkin-light"}
                    value={bddContent}
                    beforeMount={handleEditorBeforeMount}
                    onChange={handleEditorChange}
                    onMount={handleEditorDidMount}
                    options={{
                        minimap: { enabled: false },
                        fontSize: 13.5,
                        fontFamily:
                            "var(--font-jetbrains-mono), ui-monospace, SFMono-Regular, monospace",
                        wordWrap: "on",
                        scrollBeyondLastLine: false,
                        padding: { top: 16, bottom: 16 },
                        readOnly: false,
                        lineNumbersMinChars: 3,
                        folding: true,
                        renderLineHighlight: "line",
                        smoothScrolling: true,
                    }}
                />
            )}

            <ConfirmModal
                open={isConfirmingUpload}
                title="Replace the current scenarios?"
                description={
                    isDirty
                        ? "The editor has unsaved edits. Uploading a .feature file overwrites them and they cannot be recovered."
                        : "Uploading a .feature file replaces everything currently in the editor."
                }
                confirmLabel="Choose file"
                destructive
                onConfirm={openFilePicker}
                onCancel={() => setIsConfirmingUpload(false)}
            />
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

    // The editor themes track the app palette: structural keywords take the
    // keyword violet, step keywords the pass green, so the editor reads with
    // the same colour language as the verdicts beside it.
    monaco.editor.defineTheme("gherkin-light", {
        base: "vs",
        inherit: true,
        colors: {
            "editor.background": "#FFFFFF",
            "editor.lineHighlightBackground": "#F5F5F2",
            "editorLineNumber.foreground": "#A9ABA6",
            "editorLineNumber.activeForeground": "#16181D",
            "editorCursor.foreground": "#16181D",
            "editorIndentGuide.background1": "#EAEAE6",
        },
        rules: [
            { token: "keyword", foreground: "5B3FC4", fontStyle: "bold" },
            { token: "type", foreground: "1F7A4D", fontStyle: "bold" },
            { token: "comment", foreground: "8A8D88", fontStyle: "italic" },
            { token: "annotation", foreground: "A8710C" },
            { token: "string", foreground: "B4342A" },
            { token: "variable", foreground: "5B3FC4" },
            { token: "delimiter", foreground: "A9ABA6" },
        ],
    });

    monaco.editor.defineTheme("gherkin-dark", {
        base: "vs-dark",
        inherit: true,
        colors: {
            "editor.background": "#23262B",
            "editor.lineHighlightBackground": "#2A2E34",
            "editorLineNumber.foreground": "#6A7079",
            "editorLineNumber.activeForeground": "#E8E8E4",
            "editorCursor.foreground": "#E8E8E4",
            "editorIndentGuide.background1": "#33373D",
        },
        rules: [
            { token: "keyword", foreground: "A594F0", fontStyle: "bold" },
            { token: "type", foreground: "5FCF94", fontStyle: "bold" },
            { token: "comment", foreground: "8A9098", fontStyle: "italic" },
            { token: "annotation", foreground: "E0A33A" },
            { token: "string", foreground: "F0847A" },
            { token: "variable", foreground: "A594F0" },
            { token: "delimiter", foreground: "6A7079" },
        ],
    });
}
