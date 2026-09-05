# Methodology

*Written to match the structure, style, level of detail and academic tone of the
"Requirement Embedding Model for RAG" section.*

> **Placement note.** Your existing *Requirement Embedding Model for RAG* section
> describes the construction of the knowledge base and the similarity-based retrieval of
> project context. It belongs between §9 and §10 below. Section 10 extends it by
> describing how retrieval granularity and user control over enrichment are handled, and
> should be read as a continuation rather than a repetition.

---

## 1. Requirement Ingestion from the Issue Tracking System

The proposed system takes as its entry point a requirement recorded in the
organisation's issue tracking system. The user supplies either a Jira ticket identifier
or the complete URL of a Jira issue, and the system resolves both forms to a canonical
issue key before any retrieval is attempted. Accepting both forms removes a transcription
step from the user's workflow and avoids a class of input error that would otherwise
surface as an authentication or not-found failure later in the pipeline.

Retrieval is performed through the Jira REST API using token-based authentication over an
encrypted transport. The following fields are extracted from the retrieved issue:

- Summary
- Description
- Acceptance Criteria
- Labels
- Linked Issues

Acceptance criteria are recorded inconsistently across organisations and even across
projects within the same organisation. In some configurations they occupy a dedicated
custom field; in others they are embedded as a subsection of the issue description, or
are implied by the summary alone. A pipeline that assumes a single location therefore
fails on the majority of authentic tickets. To accommodate this variation, an ordered
extraction strategy is applied: a dedicated acceptance-criteria field is sought first,
and where it is absent the system degrades to the description, and finally to the
summary. The strategy is designed so that ingestion degrades in the quality of its output
rather than failing outright, since a partially useful requirement is preferable to a
rejected one.

Once extracted, the ticket content is segmented into overlapping textual chunks using
sentence-aware windowing, with each window bounded at approximately five hundred words
and successive windows overlapping by fifty words. The overlap is deliberate: an
acceptance criterion that straddles a window boundary would otherwise be fragmented
across two chunks, neither of which would be independently retrievable. Each chunk is
then converted into a dense vector representation using an embedding model and stored in
the vector database under a namespace derived from the authenticated user identifier and
the session identifier. This namespace is applied both when vectors are written and when
they are queried, so that retrieval across sessions or across users is structurally
impossible rather than merely prohibited by policy.

In parallel with vector indexing, a session record is created in the relational database
linking the session identifier to the originating ticket identifier. This record forms
the anchor to which every subsequent artefact — generated scenarios, verification
verdicts, question-and-answer history, and exported reports — is attached, and is what
allows the completed traceability chain to be attributed to a real work item rather than
to an anonymous processing run.

Failures arising from an invalid ticket reference, an authentication error, or an
unavailable external service are mapped to a uniform structured error response carrying a
machine-readable code and a human-readable message. Internal diagnostic detail is
excluded from the response, and the session state is preserved so that the user may
correct the input and retry without loss of work.

---

## 2. Behaviour-Driven Test Case Generation from Acceptance Criteria

The generation component transforms the extracted acceptance criteria into structured
behavioural scenarios expressed in the Gherkin notation. The technical difficulty
addressed here is not the production of fluent Gherkin, which contemporary large language
models perform reliably, but the preservation of **provenance**. For a verification
verdict issued later in the pipeline to be attributable to a specific requirement, each
generated scenario must carry a machine-readable reference to the acceptance-criterion
clause from which it was derived. Free-form textual generation cannot supply this,
because the association would then have to be recovered by post-hoc parsing of natural
language — reintroducing precisely the ambiguity the system is designed to eliminate.

The approach adopted is therefore **schema-constrained generation**. Rather than
requesting Gherkin as free text, the model is instructed to emit a structured JSON
document conforming to a predefined response schema, in which every scenario object
carries the following fields:

- `source_ac_clause` — the identifier of the originating acceptance-criterion clause
- `feature` — the feature grouping to which the scenario belongs
- `scenario` — the scenario title
- `given`, `when`, `then` — the behavioural steps

The inclusion of the clause reference as a mandatory generated field is the central
design decision of this component. Traceability is produced as a first-class output of
generation and validated at the service boundary, rather than reconstructed afterwards.
A response that omits the field, or that returns an empty scenario collection, is
rejected at the boundary and surfaced as a generation failure, so that malformed output
cannot propagate silently into the verification stage.

Generation is invoked through a model provider abstraction rather than through a vendor
software development kit directly. This abstraction is described in §15 and is what
permits the generating model to be substituted — for instance, exchanging a general-
purpose commercial model for a domain-specific fine-tuned model — without modification to
the generation pipeline itself. Where the model call fails, a structured error is
returned rather than a partial or unvalidated result, and the ingested session content
remains intact so that generation may be retried.

The generated scenario collection is subsequently rendered into conventional Gherkin text
for presentation to the user, while the structured representation is retained internally
for programmatic consumption by the verification, retrieval and reporting components.

---

## 3. Scenario Review, Export and Substitution

An automated pipeline whose output cannot be inspected or corrected is not adoptable in
practice, since practitioners cannot reasonably be expected to treat unverified generated
artefacts as authoritative. The system therefore places a deliberate human review point
between generation and verification.

Generated scenarios are rendered into an in-browser code editor providing Gherkin syntax
highlighting, in which every line may be freely edited. Modifications are captured in
client-side session state and reflected immediately, so that the content submitted for
verification is the content the user has reviewed rather than the raw model output. The
editor is presented in a desktop-oriented layout and is rendered read-only on narrow
viewports, reflecting the assumption that requirement review is a desk-based activity.

Two export paths are provided so that the system's output can be integrated with existing
test tooling rather than remaining confined to the platform. The first produces a
conventional `.feature` file encoded in UTF-8 and named after the originating ticket,
suitable for consumption by Cucumber-family test runners. The second parses the scenario
content into a tabular test-case representation with the columns *Scenario*, *Given*,
*When*, *Then* and *Expected Result*, exported as a comma-separated values file for import
into conventional test management systems.

A third path permits the user to upload an existing `.feature` file, which replaces the
editor content and bypasses model-based generation entirely. Beyond its practical value —
allowing verification of scenarios that a team has already authored by hand — this path
carries methodological significance for the research. It decouples the verification
contribution from the generation contribution, so that the verification component can be
exercised and evaluated against human-authored scenarios that the system did not itself
produce. This affords an evaluation design free of the circularity that arises when a
language model is used to assess the output of another language model.

---

## 4. Source Code Retrieval from the Version Control System

Verification requires that the implementation under examination be made available to the
reasoning component without obliging the user to transfer source code manually. Because
different verification intents imply different code scopes, the system supports three
retrieval modes, selected by the user at verification time:

| Mode | User input | Retrieval method |
|---|---|---|
| Exact File Paths | One or more repository file paths | Repository Contents API, invoked per file |
| Full Repository | Repository URL | Repository Trees API followed by the Contents API |
| Pull Request | Pull request URL | Pull Request Files API, returning the change diff |

The exact-path mode supports a focused examination where the relevant implementation is
already known. The full-repository mode supports a broad examination where it is not.
The pull-request mode confines the examination to a proposed change, mapping directly
onto the code review moment and permitting a developer to establish scenario coverage
before requesting review.

Access to the version control platform is authenticated using a personal access token
supplied as a deployment secret and never transmitted to the client. Requests are subject
to the platform's rate limiting, and responses indicating that a rate limit has been
exceeded trigger automatic retry with exponential back-off before any message is
surfaced to the user, so that transient throttling does not present as a failure.

Failures that cannot be resolved by retry — an invalid path, a file that does not exist,
an inaccessible repository, or an unavailable service — are mapped to human-readable
messages that identify the specific input at fault. Critically, the generated or uploaded
scenario content and the session state are preserved unchanged across such a failure, so
that the user corrects the offending input and re-attempts verification without
re-executing the earlier stages of the pipeline.

---

## 5. Agentic Verification of Scenarios Against Source Code

The verification component determines, for each behavioural scenario independently,
whether the retrieved implementation satisfies it, and returns the evidence upon which
that determination rests. Two problems govern the design. The first is **context
selection**: a repository of realistic size cannot be presented to a language model in
full, so some mechanism must decide which portions the model examines. The second is
**verdict groundedness**: a model asked whether a requirement is implemented will produce
a confident answer irrespective of whether it has examined any relevant code, so an
unconstrained design risks fluent verdicts with no evidentiary basis.

An initial design followed a conventional *retrieve-then-verify* arrangement, in which
source code was fetched first and submitted to the model together with the scenarios.
This arrangement is straightforward but requires that the context-selection decision be
made *before* the reasoning that would inform it, and it does not extend to whole-
repository verification, since the assembled context exceeds the available context window.

The adopted design instead employs **agentic verification**, in which the reasoning
component is granted controlled access to the repository and determines its own retrieval
path. Three tools are exposed to the model:

- `search_code` — keyword search across the repository
- `list_directory` — enumeration of the contents of a directory
- `get_file_contents` — retrieval of the contents of a specific file

Execution proceeds as a bounded tool-calling loop, limited to a fixed maximum number of
rounds so that cost is capped and non-termination is prevented. The loop is implemented
once within the model provider abstraction, so that the vendor-specific protocol by which
tool invocations are expressed does not propagate into the verification logic; the
verification component supplies the tool definitions and an execution handler, and the
provider drives the interaction.

The model's investigative behaviour is governed by procedural constraints expressed in the
system instruction. It is directed to attempt keyword search first; to fall back to
directory traversal from the repository root where search returns no results, as commonly
occurs for private repositories; and — as a mandatory requirement — to retrieve and read
the contents of a candidate file before forming any verdict, rather than inferring
implementation from a filename. It is further directed to follow delegation between
components, examining the implementation a file delegates to rather than terminating at
the first plausible match.

A safeguard is applied to the verdict itself. Where every tool invocation has failed and
no source file has been successfully read, the component does not permit an unsupported
verdict to be issued; the scenario is instead recorded as failed on grounds of incomplete
verification. This inverts the default behaviour of language-model judgement, which is to
answer regardless of evidence, and preserves the distinction between the finding that
*the code does not implement the scenario* and the finding that *it could not be
established whether the code implements the scenario*.

The model's concluding judgement is emitted as a structured verdict containing the
scenario identifier and title, a pass or fail status, a natural-language justification
referencing specific code constructs, a code reference identifying file, function and
line, links to the examined source locations, and — for failing scenarios only — an
actionable implementation suggestion. The verdict is passed through a tolerant JSON
recovery routine to accommodate minor malformation, then validated against a predefined
schema before acceptance. Verdicts are transmitted to the client progressively as each
scenario completes, using server-sent events, and each is persisted to the relational
database immediately upon emission so that an interrupted run retains its completed
results.

---

## 6. Presentation of Verification Outcomes

The presentation component renders verification outcomes such that a coverage gap is
identifiable and actionable without the user awaiting completion of the entire run. Since
a verification run over a realistic scenario set occupies a period of minutes, an
interface that blocks until completion would be unusable, and a bare enumeration of pass
and fail outcomes would not be actionable.

Verdicts are therefore rendered progressively as they arrive over the event stream, each
scenario appearing as an independent result row at the moment its verdict is issued. Each
row presents the verdict status, the natural-language justification referencing the
specific code examined, and a hyperlink resolving to the exact file and line upon which
the judgement was based. For failing scenarios the row additionally presents the
implementation suggestion, and such rows are expanded by default while passing rows remain
collapsed, so that the user's attention is directed toward the outcomes requiring action.

An aggregate pass rate is computed from the terminal summary event and displayed
prominently, providing an immediate indication of overall requirement coverage.

Where knowledge-base enrichment has been applied to the run, a supplementary panel
presents the project context retrieved during analysis, listing each contributing source
with an expandable extract and an outbound link to the originating document or issue.
Presenting the justification, the code reference and the retrieved context together
renders the verdict inspectable by the user, so that the basis of an automated judgement
can be examined rather than accepted on trust.

Results are re-rendered from the persisted verdict records when a session is revisited,
so that the presentation is not dependent upon the live event stream and remains available
indefinitely after the run concludes.

---

## 7. Authentication and Multi-Tenant Data Isolation

Because the platform holds requirements, documentation and source code belonging to
distinct organisations within shared infrastructure — and, in particular, within a shared
vector index — the isolation of tenant data is a precondition of the architecture rather
than an operational concern appended to it.

User identity is established through a managed authentication service supporting both
email-and-password credentials and federated OAuth providers. On the client, calls to the
authentication library are confined exclusively to server-side actions, so that session
tokens are never exposed to browser-executed code; framework middleware redirects
unauthenticated navigation before any protected view is rendered.

At the application service, a reusable dependency decodes the signed token accompanying
each request and yields the authenticated user identifier. This dependency is applied to
every protected endpoint, and requests presenting an absent, malformed or expired token
receive an unauthorised response in the standard error format. During development the
pipeline was constructed against a fixed development identifier, which this component
subsequently replaced with the authenticated identity across all services — an approach
that permitted the pipeline's behaviour to be established before authentication
complicated it, and reduced the subsequent substitution to a mechanical change.

Isolation is then enforced redundantly across four independent layers:

| Layer | Mechanism |
|---|---|
| Application | Ownership checks against the authenticated identifier before any read or write |
| Relational database | Row-level security policies binding every operation to the authenticated identifier |
| Vector database | Namespaces derived from the user identifier, applied at write and query time |
| Object storage | Bucket policies scoped to the owning user |

The redundancy is deliberate. The failure mode of a tenancy defect is disclosure of one
organisation's requirements and source code to another, which occurs silently and without
raising an error. Because the four mechanisms are unrelated — row-level security does not
constrain the vector database, and a namespace convention does not constrain relational
queries — a defect in any single layer is insufficient on its own to produce disclosure.

---

## 8. Session Persistence and Retrieval of Historical Work

Persistence is what distinguishes an instrument from a demonstration. Without it, the
traceability chain the system constructs would not survive the request that produced it,
no longitudinal record would accumulate, and the corpus upon which model specialisation
depends could not be collected.

All pipeline artefacts are persisted to a relational database through an asynchronous
object-relational mapping layer, with schema evolution managed by versioned migrations.
The schema comprises sessions, generated and uploaded scenario files, question-and-answer
history, verification verdicts, ingested knowledge sources, uploaded training datasets,
and comparative evaluation results. Every record is keyed to the owning user identifier
and governed by the row-level security policies described in §7.

Records are written at each stage of the pipeline: a session record upon requirement
ingestion; a scenario file record upon generation, upload or editorial correction; a
verdict record for each scenario as it is verified; and a message pair for each exchange
with the question-answering components. Verdicts are written incrementally rather than in
a single transaction at the conclusion of a run, so that an interrupted verification
retains the results it had already produced.

A history view lists the user's previous sessions, presenting the originating ticket
identifier, the creation date and the generation status of each. Selecting a session
restores its scenario content into the editor and renders its stored verification report,
so that prior work may be reviewed, audited or continued. Retrieval is scoped twice — by
an explicit ownership check at the application service and again by the database security
policy — so that a session belonging to another user is not returned even where its
identifier is known.

---

## 9. Artefact Storage

Artefacts that are not naturally represented as relational records — exported reports,
uploaded scenario files, uploaded training corpora and intermediate test artefacts — are
persisted to an object storage service. A single private storage container is used, within
which artefacts are separated by purpose into distinct logical folders.

All storage operations are routed through a dedicated storage service that encapsulates
the underlying storage interface, and direct invocation of the storage library from
request handlers or from other services is prohibited by an architectural rule enforced
throughout the codebase. Concentrating storage access at a single point ensures that
access-control logic exists in exactly one location rather than being re-implemented at
each call site, and reduces substitution of the storage backend to a change confined to
that service.

Container policies restrict access to the owning user, so that stored artefacts inherit
the same isolation guarantees as relational and vector data.

---

> ### *Requirement Embedding Model for RAG*
> *(your existing section is inserted here)*

---

## 10. Per-Scenario Knowledge Retrieval and Enrichment Control

The retrieval mechanism described in the preceding section establishes how project
knowledge is embedded, indexed and compared against an incoming requirement. This section
describes two refinements to how that mechanism is applied during verification: the
granularity at which retrieval is performed, and the control the user exercises over
whether it is performed at all.

**Retrieval granularity.** An initial arrangement performed retrieval once per
verification run, using the complete scenario document as the query text and supplying
the resulting knowledge to every scenario in common. Because verdicts are issued
independently per scenario, however, this arrangement mismatches the granularity of
retrieval to the granularity of the decision it informs: knowledge relevant to one
scenario is supplied to all others, diluting the context available to each. The adopted
arrangement therefore performs retrieval **per scenario**, each scenario querying the
knowledge base with its own behavioural steps and originating clause text, and receiving
only the knowledge chunks retrieved for it.

Performing retrieval per scenario multiplies the number of embedding operations and
similarity queries in proportion to the scenario count. To prevent this refinement from
imposing a proportional increase in latency, all scenario query texts are embedded within
a single batched request, and the resulting similarity queries are issued concurrently.
The precision gain is thereby obtained without a corresponding cost in elapsed time.

**Relevance filtering.** Similarity search returns a fixed number of nearest neighbours
unconditionally, irrespective of whether those neighbours bear any genuine relation to
the query. Retrieving the nearest five vectors from a knowledge base containing nothing
relevant yields five irrelevant passages, which are not merely unhelpful but actively
harmful, since they consume context budget and may mislead the reasoning component. A
minimum similarity threshold is therefore applied above the nearest-neighbour selection,
such that a query with no sufficiently similar material yields no context at all rather
than weakly related context.

**User control over enrichment.** Whether the knowledge base influences a given
verification run is determined by the user at the point of verification, through an
explicit opt-in presented alongside the verification controls and disabled by default.
When enrichment is declined, the knowledge base is not queried, no project context is
introduced into the reasoning prompt, and the retrieved-context field of every verdict is
empty. When it is accepted, per-scenario retrieval proceeds as described above and the
retrieved context is incorporated into each scenario's prompt.

Two considerations motivate this design. The first is user agency: the material that
influences a verification verdict should be the user's decision rather than a deployment-
wide configuration, particularly where the knowledge base may contain outdated or
contested project documentation. The second is methodological: because enrichment is
selectable per run rather than fixed per deployment, the same session may be verified
twice under otherwise identical conditions, affording a paired experimental design for
assessing the contribution of retrieval to verdict quality.

Retrieval degrades gracefully in all failure conditions. Where the knowledge base is
empty, where the vector database is unconfigured, or where retrieval exceeds its time
budget, verification proceeds without enrichment rather than failing. The knowledge
retrieved for each scenario is persisted alongside that scenario's verdict, so that an
enriched verdict carries the evidence of its own enrichment and can be examined after the
fact.

---

## 11. Requirement-Scoped Question Answering

Comprehension of a requirement is a precondition for validating the scenarios derived
from it: a practitioner cannot reasonably assess whether generated scenarios faithfully
represent an acceptance criterion without first understanding that criterion. Extensive
acceptance-criteria documents are, however, slow to read in full, and the questions
practitioners characteristically ask of them — concerning edge cases, exceptions and
implicit assumptions — are not answerable by keyword search.

The system therefore provides a conversational interface scoped to a single ingested
requirement. A user question is converted into an embedding vector and compared against
the chunks of the ingested ticket held under that session's namespace, and the most
similar chunks are supplied to the language model as the sole basis for its response.

The governing constraint upon this component is **groundedness**. An assistant that
answers from a model's parametric knowledge rather than from the ingested requirement is
worse than no assistant at all, because its errors are fluent, plausible and
unattributable. Groundedness is consequently enforced by three mechanisms acting
together. Architecturally, retrieval is confined to a namespace derived from the
authenticated identity and the session, so that content outside the requirement under
examination cannot be reached. At the level of instruction, the model is directed to
answer exclusively from the supplied context and to respond explicitly that the
information is not present in the ticket where retrieval returns nothing. At the level of
sampling, a low temperature is applied to suppress speculative continuation.

Responses are streamed to the client token by token so that the user receives output
progressively rather than awaiting the complete answer, with an indicator displayed until
the first token is received. Each question-and-answer pair is persisted against the
session and the authenticated user, so that the interrogation history forms part of the
session record and is re-displayed when the session is revisited.

---

## 12. Project-Scoped Question Answering

The component described in §11 is confined to a single requirement. Questions concerning
architectural conventions, historical decisions or related prior work necessarily span
multiple sources and have no single requirement to which they may be anchored. A second
conversational interface therefore operates over the entire project knowledge base
described in the *Requirement Embedding Model for RAG* section — the ingested Confluence
documentation, workspace issues and uploaded project documents held under the user's
knowledge namespace.

This component reuses the retrieval mechanism of the knowledge base and the streaming
mechanism of the requirement-scoped interface, differing principally in scope and in
state. Because no session exists to which a project-wide question could be anchored, the
interface is stateless and conversational history is not retained — an explicit design
compromise accepted in exchange for the broader retrieval scope.

Following the streamed answer, the component emits an enumeration of the knowledge sources
from which the response was drawn, rendered as selectable citations resolving to the
originating documentation page or issue. Because the answer names and links its sources,
the user is able to verify its grounding directly rather than relying upon the assertion
that grounding was enforced — a stronger transparency property than the requirement-scoped
interface provides.

The same grounding triad applies: namespace-confined retrieval, an instruction forbidding
answers outside the supplied context together with an explicit acknowledgement where the
knowledge base contains nothing relevant, and low-temperature sampling. Retrieval failure
degrades to empty context rather than to an error response, so that an unconfigured or
unavailable vector database yields a graceful acknowledgement rather than a service
failure.

---

## 13. Traceability Report Construction

The traceability report is the artefact that the research problem ultimately demands: a
durable record linking each acceptance criterion to the scenario derived from it, the
verdict returned for that scenario, and the code evidence upon which the verdict rests.

The report is assembled from the persisted verification verdicts joined with the persisted
scenario content. Each row of the report comprises the acceptance-criterion clause, the
scenario title, the verification status, the natural-language justification, the code
reference, the implementation suggestion where the scenario failed, and the project
context retrieved during analysis where enrichment was applied.

The governing design decision of this component is that **no language model is invoked
during report construction**. The report is a deterministic projection of persisted
evidence rather than a generated summary of it. Three properties follow. The report cannot
introduce a claim that is absent from the underlying verdicts, since it contains no
generative step at which such a claim could be introduced. Repeated construction from
unchanged underlying data yields identical output, so that the artefact is reproducible.
And the artefact is consequently admissible as evidence of what the system determined,
rather than as a narration of it.

This decision is counter-intuitive within a system whose other components are
model-driven, and it reflects a general principle applied throughout the architecture:
stochastic behaviour is confined to those stages where judgement is genuinely required —
scenario derivation and code verification — while the stage that produces the audit
artefact is a pure function of persisted state.

Retrieval of a session's report is confined to the session's owner, enforced both by an
explicit ownership check at the application service and by the database security policy.

---

## 14. Report Export

The traceability report is rendered into two portable formats so that verification
evidence may leave the platform and be attached to a pull request, an issue, or a
communication to stakeholders.

The first is a formatted document produced through a document generation library,
presenting the traceability rows in tabular form together with the retrieved project
context and the implementation suggestions. The second is a comma-separated values file
carrying the columns *AC Clause*, *Scenario*, *Status*, *Justification*, *Code Reference*,
*Implementation Suggestion* and *RAG Context*, suitable for import into spreadsheet and
test management tools.

A security measure is applied to the tabular export. Because justification and suggestion
text originates from a language model and is routinely opened in a spreadsheet
application, every exported cell is passed through a sanitisation routine that neutralises
spreadsheet formula injection. This reflects a consistent posture maintained throughout
the system, in which model-generated text is treated as untrusted input at every boundary
it crosses.

Generated exports are persisted to object storage through the storage service described
in §9 and remain retrievable thereafter. In common with report construction, no model is
invoked during export: the exported artefacts are assembled entirely from persisted
records. Export is available only where verification results exist for the session, and
the corresponding controls remain disabled otherwise.

---

## 15. Dual-Model Provider Abstraction

The system maintains two independent abstractions over model access, reflecting the fact
that it employs language models for two categorically different purposes.

| Abstraction | Purpose | Implementations |
|---|---|---|
| General-purpose model provider | Code verification, question answering, generation fallback | Commercial hosted models and a locally served open model |
| Scenario generation model provider | Behaviour-driven scenario generation | A domain-specific fine-tuned model, or delegation to the general-purpose provider |

Each abstraction is realised as an abstract interface with concrete implementations
selected at runtime by an environment configuration value, instantiated through a factory.
The generation service routes exclusively through the scenario generation abstraction and
never invokes a model interface directly — a constraint enforced as an architectural rule
throughout the codebase.

The two model paths are independently substitutable, so that the unavailability of one
does not preclude use of the other. The fallback implementation of the scenario generation
abstraction delegates to the general-purpose provider, which ensures that generation
remains available when no specialised model is deployed or when a deployed specialised
model is unreachable.

The timing of this abstraction's introduction is methodologically significant. It was
implemented at the outset of development, **before any specialised model existed to be
substituted into it**. Constructing the substitution seam in advance of the artefact to be
substituted converted the subsequent comparative study from an exercise requiring
modification of the generation pipeline into one requiring only a configuration change,
with the consequence that the comparison could be conducted through the production code
path rather than through a parallel experimental one.

---

## 16. Training Data Capture and Consent Governance

Specialisation of a model for scenario generation requires a corpus of authentic pairs,
each associating an acceptance criterion with the scenarios derived from it. The
application as originally constructed persisted neither element of such a pair: the
acceptance-criteria text supplied to generation was not retained, and editorial
corrections made by the user were discarded when the session concluded. Since these pairs
cannot be reconstructed retrospectively, every period of operation without a capture
mechanism represents a permanent loss of training data. The capture component was
therefore implemented and deployed ahead of the remainder of the specialisation work,
notwithstanding that the model itself belonged to a later development phase.

**Capture.** Upon successful generation, the acceptance-criteria text that produced the
output is persisted against the resulting scenario record, forming a retrievable
input-to-output pair. When a user subsequently edits generated scenarios and saves them, a
**distinct record** is written and marked as an editorial revision, referencing the
originating generated record rather than replacing it. Preserving both versions retains
the difference between the model's output and the human-corrected output, which
constitutes the most informative training signal the system is capable of collecting,
since it identifies precisely where the model's output required correction.

**Consent governance.** Whether captured content may be used for model training is
governed by a deployment configuration value. The design decision of record is that this
value is **stamped onto each record at the moment of writing, and never evaluated at the
time a dataset is constructed**. Consent is held to belong to the moment of capture, so
that a subsequent change to the configuration must not retroactively reclassify data that
already exists. Records are persisted in full under either setting — session history and
diagnostic capability are unaffected — and only their eligibility for training is altered.
The dataset construction procedure excludes ineligible records and reports the number
excluded, so that a corpus that is empty by reason of consent is never mistaken for a
corpus that is empty by reason of a database fault.

**Supplementary corpus acquisition.** Because organic capture accumulates slowly in
proportion to system usage, an authenticated upload facility permits a researcher to
contribute existing scenario files or pre-formatted training datasets directly. Uploaded
scenario files are parsed and rejected where they contain no usable scenario, so that
unusable material cannot silently accumulate within the corpus. Uploaded datasets are
validated line by line against the required pair structure, and where validation fails the
first offending line number is reported rather than a generic failure, so that the
contributor may correct the specific defect. Uploads are stored through the storage
service, recorded against the contributing user, enumerated with their file name, type
and pair count, and may be individually deleted. Access is confined to the contributing
user.

The dataset construction procedure accepts three corpus sources in combination: files
present on the filesystem, records captured from application usage, and datasets
contributed through the upload facility.

---

## 17. Fine-Tuning Dataset Construction

Model specialisation requires a corpus of paired acceptance criteria and reference
scenarios. As described in §16, the capture mechanism had not yet accumulated authentic
pairs at the point at which specialisation was undertaken. The dataset was therefore
constructed by a **back-generation** procedure, which proceeds in the reverse of the
direction the model is intended to operate.

Human-authored scenario files were collected from a set of permissively licensed public
repositories, forming a corpus of genuine behavioural specifications. Each file was parsed
and subjected to quality filtering, retaining only documents containing complete
behavioural step structures and rejecting those containing placeholder or unfinished
content. A language model was then employed to **reconstruct the acceptance criteria that
would plausibly have produced each retained document**.

The consequence of this procedure is an asymmetry in the authenticity of the resulting
pairs, and the asymmetry is stated explicitly because it bears upon the interpretation of
every result derived from the corpus. The **output** side of each pair is genuine
human-authored behavioural specification. The **input** side is synthetic, reconstructed
by a model from the output it is intended to produce. An acceptance criterion derived from
its own reference describes that reference by construction, which flatters any model that
reproduces it.

Each constructed pair was emitted in a conversational training format and validated
against the same response schema that governs generation at runtime, with pairs producing
an empty scenario collection rejected. The corpus was divided into training and holdout
partitions **by originating file** rather than by individual pair, so that near-duplicate
scenarios drawn from the same source document cannot appear on both sides of the division.
The disjointness of the two partitions was verified after construction and again
immediately prior to training.

The procedure was subsequently extended to expand parameterised scenario templates by
substituting their example rows, subject to a cap of three substitutions per template so
that a single large parameter table could not saturate the corpus with near-identical
scenarios.

The construction yielded a corpus of the following proportions:

```
708 scenario files scanned → 213 documents retained → 182 pairs constructed
                             (31 discarded during back-generation)
                          → 164 training / 18 holdout, divided by originating file
```

The composition of the retained corpus is recorded because it materially conditions the
result: the substantial majority of the retained documents originate from the test suites
of testing frameworks, in which the behavioural specifications describe the execution of
command-line operations rather than the behaviour of application features.

---

## 18. Domain-Specific Model Training

The specialised model was produced by parameter-efficient fine-tuning of an open
instruction-tuned base model, under the constraint that training must complete on freely
available accelerator hardware.

Training the full parameter set of a seven-billion-parameter model does not fit within the
sixteen gigabytes of memory available on the target accelerator. Two techniques were
combined to bring the procedure within that bound. First, the base model was loaded under
four-bit quantisation, reducing the memory occupied by its weights. Second, low-rank
adaptation was employed, in which the base parameters are frozen entirely and small
adapter matrices are inserted into the attention and feed-forward projections and trained
in their place. The product of training is therefore not a new model but a compact adapter
that is applied to the base model at inference time.

The training configuration was as follows:

| Parameter | Value |
|---|---|
| Base model | Instruction-tuned seven-billion-parameter open model |
| Quantisation | Four-bit, normalised float, double quantisation, half-precision compute |
| Adaptation method | Low-rank adaptation — rank 16, scaling factor 16, dropout 0.0 |
| Adapted modules | Query, key, value and output projections; gate, up and down projections |
| Trainable parameters | 40,370,176 of 7,655,986,688 — 0.527 % |
| Learning rate | 2.0 × 10⁻⁴, linear schedule with 5 % warm-up |
| Epochs | 3 |
| Effective batch size | 8 (batch of 2 with 4-step gradient accumulation) |
| Maximum sequence length | 2,048 tokens |
| Random seed | 3407 |
| Accelerator | Single mid-range GPU, free tier |

An accelerated training library was employed initially and subsequently abandoned, after
two runs terminated within its modified training routine owing to a version incompatibility
that persisted after local version constraints were removed. It was replaced with the
standard transformer training stack, which completed training on the first attempt. Since
the corpus comprises only 164 examples, no material reduction in training time was
forfeited, and the replacement offers a substantially more stable interface.

One consequence of that substitution is recorded because it affects the validity of
subsequent serving. The abandoned library provided the export path that carried the
conversation template used during training into the serving environment. Its removal
eliminated that guarantee. Rather than reconstructing the export, the training procedure
was modified to write the training conversation template alongside the produced adapter,
so that the serving component may compare the template actually applied at inference
against the template applied during training, and report any divergence. Making the gap
observable was preferred to asserting a guarantee that no longer held.

---

## 19. Serving and Integration of the Specialised Model

Integrating the trained adapter into the generation pipeline required its conversion into
a servable artefact, its exposure over a network interface, and its incorporation behind
the generation abstraction described in §15 without exposing users to the experiment.

The trained adapter was converted into a quantised serving format and registered with a
local inference runtime against the corresponding base model. A lightweight service layer
was then constructed to mediate between the application and that runtime. This layer
implements precisely the request and response contract that the generation abstraction
expects — receiving acceptance criteria, a system instruction and a response format
specification, and returning a document conforming to the generation response schema — and
validates its own output against that schema before responding, so that malformed model
output is rejected at the serving boundary rather than propagating into the application.

Because the conversation template applied at inference must correspond to the template
applied during training for the specialised behaviour to manifest, the service layer
compares the template configured in the inference runtime against the template persisted
alongside the adapter, and reports the comparison as matching, diverging or
indeterminate. An indeterminate result is treated as a failure rather than as a pass, on
the reasoning that an unverifiable guarantee is not a guarantee.

Three properties are enforced on the application side of the integration. A **total
elapsed-time bound** is applied to the request, deliberately set well below the overall
generation budget so that, in the event the specialised endpoint does not respond, the
fallback to the general-purpose model still completes within the budget the user
experiences. The bound is applied as a total rather than a per-phase constraint, since a
per-phase timeout cannot cap the duration of the exchange as a whole. **Automatic
fallback** to the general-purpose provider occurs where the endpoint is unreachable,
exceeds its time bound, or returns unparseable output, so that the user is never exposed
to a failure of the experimental path. Finally, because fallback is silent by design,
**explicit instrumentation** records for each generation the provider that was configured,
the provider that in fact served the request, and the reason for any divergence.

This instrumentation is not merely operational but methodologically necessary. Since the
output of the fallback path is indistinguishable in form from the output of the
specialised path, the recorded provenance is the only reliable means of establishing which
model produced a given result. An evaluation conducted under the production time bound
would have fallen back on every request and, absent this instrumentation, would have
compared the general-purpose model against itself while appearing to compare two distinct
systems.

---

## 20. Comparative Evaluation of the Specialised and General-Purpose Models

The evaluation component determines whether the specialised model produced by §17 and §18
outperforms the general-purpose model on the scenario generation task, and does so through
a procedure designed to resist the biases to which comparisons of this kind are
susceptible.

**Evaluation set.** The evaluation was intended to be conducted over authentic
requirements drawn from the issue tracking system. Measurement of the operational database
established that this premise did not hold: no captured acceptance-criteria pairs existed,
and only a small number of distinct tickets had ever been ingested. The evaluation set was
therefore assembled from the material that did exist, with each item explicitly labelled
according to the provenance by which it came to exist, and divided into two groups that
are **reported separately and never averaged**:

| Group | Items | Criteria provenance | Reference scenarios | Scoring available |
|---|---|---|---|---|
| Off-domain | 18 | Back-generated from reference | Human-authored | Structural, reference similarity, blinded judgement |
| On-domain | 10 | Author-composed for evaluation | None | Structural, blinded judgement |

The separation is the methodological core of the design. The off-domain group is drawn
from the holdout partition of the training corpus and is therefore *within the
distribution the specialised model was trained upon and outside the distribution the
application operates within*. The on-domain group comprises product-behaviour acceptance
criteria of the kind the application converts in practice, and is therefore the reverse.
Neither group alone supports a conclusion; **the difference in performance between the two
groups is the more informative measurement, since it distinguishes a model that has
learned the form of the notation from one that has learned the domain**.

**Objective metrics.** Four metrics were computed, each defined so as to be reproducible
and unambiguous rather than sophisticated:

- **Acceptance-criteria coverage** — the proportion of criteria clauses for which at least
  one generated scenario cites that clause. This measures breadth of coverage rather than
  volume of output.
- **Duplicate scenario rate** — the proportion of generated scenarios that closely
  reproduce an earlier scenario within the same output. This metric exists specifically to
  detect a model inflating its apparent coverage by restating itself.
- **Reference alignment** — lexical correspondence with the human-authored reference
  scenarios. Computable for the off-domain group only, and deliberately unsophisticated: it
  measures reproduction of the reference's language rather than the correctness of the
  output.
- **Generation success rate and latency** — the proportion of requests returning usable
  output, and the elapsed time per generation.

**Blinded subjective assessment.** In addition to the objective metrics, the paired outputs
were assessed by a language model acting as judge, scoring each on relevance to the
acceptance criteria, correctness and specificity. Outputs were anonymised as System A and
System B, with the mapping between label and originating provider drawn independently for
each item, so that neither the provenance of an output nor its position in the comparison
is inferable by the judge.

**Conflict-of-interest detection.** A check is applied to determine whether the judging
model is itself one of the models being compared, or belongs to the same model family as
one of them. Where such a conflict is detected it is reported alongside the resulting
scores rather than suppressed, so that the reader may weight the subjective assessment
accordingly. In the conducted evaluation this check identified a conflict, and the
subjective scores are consequently presented as indicative, with the objective metrics
carrying the substance of the comparison.

**Integrity safeguards.** Because the integration described in §19 falls back silently to
the general-purpose model, an evaluation could trivially compare that model against itself
and report a tie. Three safeguards address this. A configuration check verifies before
execution that the specialised endpoint is reachable and that the time bound has been
raised sufficiently for the specialised model to respond. A trust gate excludes from the
results any row whose recorded provenance indicates that fallback occurred, so that only
generations genuinely served by the specialised model contribute to its scores. And the
number of excluded rows is reported alongside the results, so that a comparison compromised
by widespread fallback is visible rather than concealed.

Results are persisted to the database keyed by model version and evaluation item, so that
comparisons may be tracked longitudinally across successive specialised models, and the
evaluation procedure is resumable so that an interrupted run need not be repeated in full.
The procedure is invoked as a command-line operation and emits a summary report presenting
the two model outputs side by side.
