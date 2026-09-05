# Methodology

Written in the same structure as the *Requirement Embedding Model for RAG* section.
Your existing section fits between Section 9 and Section 10 below.

---

## 1. Jira Ticket Fetching

The user enters a Jira ticket ID or a full Jira URL. Both formats are accepted. The system
reads the ticket key from the input and then calls the Jira REST API using an API token.

The following fields are read from the ticket:

- Summary
- Description
- Acceptance criteria
- Labels
- Linked issues

Acceptance criteria are not stored in the same place in every Jira project. Some teams use
a separate custom field. Other teams write the criteria inside the description. For this
reason the system checks the dedicated acceptance criteria field first. If that field is
empty, the description is used instead. If the description is also empty, the summary is
used. This order allows the system to work with different Jira setups.

After the content is read, it is split into chunks of about 500 words. Each chunk overlaps
the previous chunk by 50 words. The overlap is used so that a criterion is not cut in half
between two chunks. Each chunk is converted into a vector and stored in the vector
database. The storage name includes the user ID and the session ID, so one user cannot
read another user's data.

A session record is also created in the relational database. It links the session to the
Jira ticket. All later data, such as generated scenarios and verification results, is
attached to this session.

If the ticket cannot be fetched, the system returns a short message that explains the
problem. Internal error details are not shown to the user.

---

## 2. BDD Test Case Generation

The acceptance criteria taken from the ticket are sent to an LLM. The model returns test
scenarios in Gherkin format, using the Feature, Scenario, Given, When and Then structure.

The model is not asked to write free text. It is asked to return a JSON object with a
fixed structure. Each scenario in the JSON contains these fields:

- `source_ac_clause`, which names the acceptance criterion the scenario came from
- `feature`
- `scenario`
- `given`, `when` and `then`

The `source_ac_clause` field is the most important part of this design. It links every
scenario back to one acceptance criterion. The model writes this field itself during
generation, so the system does not have to guess the link afterwards by reading the text.
This link is later used by the verification stage, by the traceability report, and by the
model evaluation.

The response is checked against the schema before it is accepted. A response with no
scenarios, or a scenario without a clause reference, is rejected. The valid JSON is then
converted into normal Gherkin text and shown to the user.

---

## 3. Editing, Downloading and Uploading BDD Files

Generated scenarios are shown in a code editor inside the browser, with Gherkin syntax
highlighting. The user can change any line before moving to the next stage. Changes are
kept in the browser state, so the content sent for verification is the content the user
has reviewed.

Two download options are provided. The first one saves the content as a `.feature` file,
which can be used by Cucumber and similar test tools. The second one converts the
scenarios into a table and saves it as a CSV file with the columns Scenario, Given, When,
Then and Expected Result. This file can be imported into a test management tool.

The user can also upload an existing `.feature` file. In that case the uploaded content
replaces the editor content and no model is called. This option is useful for teams that
already write their own scenarios. It is also useful for the research, because it allows
the verification stage to be tested with scenarios that were written by a human and not by
a model.

---

## 4. Source Code Collection from GitHub

Before verification can run, the system needs access to the source code. Three input modes
are provided, and the user selects one:

- **Exact file paths.** The user lists one or more file paths. The system reads each file
  through the GitHub Contents API.
- **Full repository.** The user gives a repository URL. The system reads the file tree and
  then the file contents.
- **Pull request.** The user gives a pull request link. The system reads the diff of that
  pull request.

The three modes support different situations. The first mode is used when the developer
already knows which files matter. The second mode is used when this is not known. The
third mode is used during code review, when only the new changes need to be checked.

A GitHub access token is used for authentication. The token is stored as an environment
variable and is never sent to the browser. If GitHub returns a rate limit response, the
system waits and retries automatically before showing any message to the user.

If a file path is wrong or a repository cannot be reached, a clear message is shown. The
BDD content and the session data are not deleted, so the user can correct the input and
run verification again without repeating the earlier steps.

---

## 5. Code Verification Using an LLM Agent

This stage checks whether the source code implements each BDD scenario. Every scenario is
checked separately, and a separate result is returned for each one.

A simple approach would be to collect the code first and send all of it to the model with
the scenarios. This approach was tried, but it has two problems. A large repository does
not fit into the context window of a model, and the decision about which files to send has
to be made before the model has read anything.

For this reason an agent-based approach is used. The model is given three tools and is
allowed to search the repository by itself:

- `search_code`, which searches the repository by keyword
- `list_directory`, which lists the files in a folder
- `get_file_contents`, which reads one file

The model decides which tool to call and when to stop. The loop is limited to a maximum of
ten tool rounds, so the cost stays under control and the process always ends.

The model is given clear instructions about how to search. It should try keyword search
first. If the search returns nothing, which often happens with private repositories, it
should list the root folder and then look inside the folders that seem relevant. Before
giving any result, it must open and read the file content. It is not allowed to decide
based on a file name alone.

One safety rule is applied to the final answer. If every tool call failed and no file was
actually read, the system does not accept a result. The scenario is marked as failed
because verification was incomplete. This rule keeps two different situations apart. The
first is that the code does not implement the scenario. The second is that the system
could not check it. Without this rule, a model would give a confident answer in both
cases.

The final answer is returned as JSON and contains:

- The scenario ID and title
- A pass or fail status
- A justification in normal language, which refers to specific code
- A code reference with the file, function and line number
- Links to the code that was read
- An implementation suggestion, which is only filled in when the status is fail

The result is validated against a schema and then sent to the browser as soon as it is
ready, one scenario at a time. Each result is saved to the database immediately, so
results are not lost if the run is stopped.

---

## 6. Showing the Verification Results

A verification run takes some minutes when there are many scenarios. For this reason the
interface does not wait for the whole run to finish. Each result is shown as soon as it
arrives.

Every scenario is shown as one row. The row contains the pass or fail status, the
justification, and a link to the exact file and line that the model read. Failed rows are
opened automatically and show the implementation suggestion. Passed rows stay closed. This
draws the user's attention to the rows that need work.

An overall pass percentage is shown at the top of the panel. When the knowledge base was
used during the run, a second panel shows the project documents that were retrieved.

The results are stored in the database, so they are shown again when the user opens the
session later. The display does not depend on the live connection.

---

## 7. RAG Chat for a Single Jira Ticket

Long acceptance criteria take time to read, and simple keyword search does not answer
questions such as "what are the edge cases here?". A chat interface is provided so that
the user can ask questions about the ticket in normal language.

The question is converted into a vector and compared with the chunks of the ingested
ticket. Only the chunks stored under the current session are searched. The most similar
chunks are then sent to the LLM as the only source for the answer.

Two different models are used in this step, and they have different jobs. The embedding
model only converts text into vectors so that the search can compare them. It does not
write anything and it does not understand the question. The LLM does no searching. It
receives the chunks that the search has already selected, and its task is to read them and
write an answer in normal language. The LLM never sees the whole ticket. It only sees the
parts that the search returned.

The contribution of the LLM here is the answer itself. Vector search alone can only return
pieces of text that look similar to the question. It cannot join two separate parts of the
ticket, and it cannot reply to a question such as "which cases are not covered?". The LLM
reads the returned pieces together and produces one direct answer.

The main requirement for this feature is that the answer must come from the ticket. An
assistant that answers from its own general knowledge is worse than no assistant, because
the wrong answer still sounds correct. Three methods are used together to control this:

- Search is limited to the current session, so no other content can be reached
- The prompt tells the model to answer only from the given text, and to say clearly when
  the information is not in the ticket
- A low temperature setting is used, which reduces invented content

The answer is streamed to the browser word by word, so the user does not wait for the full
response. Each question and answer pair is saved to the database and shown again when the
session is opened later.

---

## 8. Building the Knowledge Base from Project Documents

The knowledge base is not limited to Jira tickets. Three types of source can be added:

- **Confluence pages**, added by space key or page ID through the Confluence REST API
- **Jira project issues**, added by project key, with optional filters such as sprint or
  label
- **Uploaded documents** in PDF or DOCX format, read with a PDF library and a Word library

All three sources use the same steps. The text is extracted, split into chunks, converted
into vectors, and stored in the vector database under a name that contains the user ID.
This means the knowledge base of one user is never visible to another user.

No LLM is used in this step. Only the embedding model is used, because the task here is to
store the documents in a form that can be searched later. The LLM is used afterwards, when
a question or a scenario needs an answer that is based on these documents. Keeping the two
models apart also keeps the cost low, because the documents are embedded once, while the
LLM is only called when the user actually asks for something.

Every added source is also recorded in a database table with its type, title, URL and
date. A reference to the stored vectors is saved at the same time. This reference is later
used to delete the vectors of one source without clearing the whole knowledge base.
Deletion is useful when a document becomes out of date, because old information would
otherwise keep affecting the results.

Uploaded files are validated before processing. Files of the wrong type and files that are
too large are rejected with a clear message.

Progress is streamed while a source is being added, so the user can see how many pages or
issues have been processed.

---

> **Your existing section, *Requirement Embedding Model for RAG*, fits here.**

---

## 9. Using the Knowledge Base During Verification

The previous section explains how project documents are stored and compared. This section
explains how the retrieved information is used during verification, and how the user
controls it.

In the first version, the knowledge base was searched once for the whole run. The complete
BDD document was used as the search text, and the same results were given to every
scenario. This was later changed. Because a separate result is produced for each scenario,
the search is now done for each scenario as well. Every scenario searches the knowledge
base with its own Gherkin text and receives only its own results.

Searching for each scenario means more search operations. To avoid a slower run, all
scenario texts are converted into vectors in a single request, and the searches are then
run in parallel.

A minimum similarity score is also applied. A vector search always returns a fixed number
of nearest results, even when nothing in the knowledge base is related to the question.
Without a minimum score, a scenario would receive five weak matches that add no value and
take space in the prompt. With the minimum score, a scenario with no related documents
receives no extra context at all.

The retrieved text is then added to the prompt of the verification model. For each scenario
the LLM receives three inputs together:

- The scenario itself, in Gherkin form
- The source code that the agent has read from the repository
- The project documents that the search has returned

The LLM reads all three and decides whether the code implements the scenario. The search
does not decide anything by itself. It only chooses which documents the LLM is allowed to
see. This is where the LLM adds value in the RAG process. A search can find a Confluence
page about the login rules, but it cannot say whether the code follows those rules. The
LLM compares the rule in the document with the code in front of it and gives a reason for
its answer.

The documents that were used are saved together with the result, so the user can see which
information influenced each decision.

The user decides for each run whether the knowledge base is used. A checkbox is shown next
to the verification button, and it is not selected by default. When it is not selected,
the knowledge base is not searched and no project context is added. When it is selected,
each scenario receives its own context and the retrieved documents are saved with the
result.

This design has two reasons. The first reason is user control, because the documents that
influence a result should be the user's choice. The second reason is research related. As
the setting is chosen per run, the same session can be verified twice under the same
conditions, once with the knowledge base and once without it. This makes a direct
comparison possible.

If the knowledge base is empty, or if the vector database is not available, verification
continues without the extra context instead of failing.

---

## 10. RAG Chat Using the Project Knowledge Base

The chat described in Section 7 is limited to one ticket. Some questions are about the
project as a whole, for example questions about the system design or about earlier work.
These questions cannot be answered from a single ticket.

A second chat interface is therefore provided. It searches the whole knowledge base of the
user, which contains the Confluence pages, the Jira project issues and the uploaded
documents. The same rules are applied as in the ticket chat. Search is limited to the
user's own data, the prompt tells the model to answer only from the retrieved text, and a
low temperature is used.

The role of the LLM is larger here than in the ticket chat, because the retrieved chunks
now come from different sources. One chunk may come from a Confluence page, another from
an old Jira issue, and a third from an uploaded PDF file. Without the LLM the user would
receive a list of separate text fragments and would have to read them all. The LLM reads
the fragments together and writes one answer that combines them. It also has to handle the
case where two documents do not agree, and the prompt tells it to report what the documents
say instead of choosing an answer of its own.

After the answer, the system also returns a list of the documents that were used. Each
document is shown as a link, so the user can open the original page and check the answer.
This is a useful difference from the ticket chat, because the user can confirm where the
answer came from.

This chat does not keep a history. There is no session to attach the messages to, so each
question is handled on its own.

---

## 11. User Accounts and Data Separation

The system stores requirements, documents and source code that belong to different users.
For this reason, separating user data is part of the design and not an extra feature.

User accounts are handled by a managed authentication service, which supports email and
password login as well as OAuth. In the browser, the authentication library is only used
inside server actions, so tokens are never available to client-side JavaScript. Users who
are not logged in are redirected before any protected page is shown.

On the server side, every protected endpoint reads the signed token and gets the user ID
from it. A request with a missing or expired token receives an unauthorised response.

Data separation is then applied in three places at the same time:

- In the application, where the user ID is checked before any read or write
- In the relational database, where row level security rules limit every query to the
  owner
- In the vector database, where the storage name always contains the user ID

These three methods are not connected to each other. Database rules do not control the
vector database, and the naming rule does not control SQL queries. Using all three means
that one mistake alone is not enough to expose another user's data.

---

## 12. Saving Sessions and Viewing Past Work

All results are saved so that work is not lost when the browser is closed.

The database stores sessions, generated and uploaded scenario files, chat messages,
verification results, knowledge base sources, uploaded training data and evaluation
results. Every record is linked to the user ID.

Records are written at each stage. A session row is created when the ticket is fetched. A
scenario file row is created when scenarios are generated, uploaded or edited. A
verification result row is created for every scenario as soon as it is checked. Results
are saved one by one during the run and not all together at the end, so a stopped run
keeps the results it has already produced.

A dashboard lists the past sessions of the user with the ticket ID, the date and the
status. When a session is opened, the saved scenarios are loaded into the editor and the
saved verification results are shown again.

---

## 13. Traceability Report and Export

The traceability report is the final output of the system. It connects each acceptance
criterion to the scenario that came from it, to the verification result of that scenario,
and to the code that was checked.

The report is built from the saved verification results and the saved scenario files. Each
row contains the acceptance criterion, the scenario title, the pass or fail status, the
justification, the code reference, the implementation suggestion for failed scenarios, and
the project documents that were used.

No LLM is used when the report is built. This is an important decision. The report is only
a view of data that is already stored, so it cannot add anything that is not in the
results. Building the same report twice always gives the same output. If a model were used
here, two exports of the same session could differ, and the report could not be used as
evidence.

The report can be downloaded as a PDF file or as a CSV file. Text that comes from the
model is cleaned before it is written into the CSV file, because spreadsheet software can
run formulas that are hidden inside cell values. Exported files are saved to file storage
and can be downloaded again later.

---

## 14. Switching Between the General Model and the Fine-Tuned Model

BDD generation uses one interface with two implementations. One calls the general model, the
other calls the fine-tuned model endpoint. An environment variable selects which. The
generation service never calls a model directly, so switching models is only a configuration
change.

---

## 15. Collecting Training Data

A fine-tuned model needs pairs: a criterion in, scenarios out. The application did not save
either at first, and this data cannot be recovered later, so collection was added before the
model work started.

When generation succeeds, the criteria are saved with the scenarios. When a user edits and
saves scenarios, a new row is written and the original is kept. The difference between the
two is the most useful training signal the system can collect.

A setting controls whether saved data may be used for training. Its value is written into
each row when the row is created and never re-read later, because consent belongs to the
moment of collection. Users can also upload their own feature files, because collected
data grows slowly.

---

## 16. Building the Fine-Tuning Dataset

The dataset uses real test scenarios, written by developers in projects. 
---

## 17. Training the Fine-Tuned Model

The model was trained with QLoRA on a free GPU service. The base model was loaded in four bit
form and only a small set of extra layers was trained, with the base frozen. The result is
not a new model. It is a small adapter file, attached to the base model when it runs.

| Setting | Value |
|---|---|
| Base model | Qwen2.5, 1.5 billion parameters, four bit |
| Method | LoRA, rank 16, alpha 16 |
| Learning rate | 2.0e-4 |
| Epochs | 3 |
| Sequence length | 2048 |
| Hardware | One Tesla T4 GPU, free tier |
| Training time | 6.9 minutes |

The chat template used in training is saved beside the adapter, because serving must use the
same one.

Larger base models could not be tested. The free GPU service used for training does not have
enough memory to hold them, so their effect on accuracy is unknown.

---

## 18. Serving the Fine-Tuned Model

The adapter was converted for a local inference tool and registered together with the base
model. A small service sits between the application and that tool, and checks its output
against the schema before replying.

If the endpoint is unreachable, too slow, or returns invalid output, the system falls back to
the general model without showing an error. Because that fallback is silent, a log line
records which model was configured, which one answered, and the reason for any difference.

---

## 19. Comparing the Two Models

The last stage compares the two models on the same inputs. Each model receives the same
acceptance criteria and returns scenarios. The evaluation set combines holdout items, which
carry developer-written reference scenarios, with ten product-domain criteria prepared for
the evaluation. Every item is labelled with its origin, so the two groups can be reported
separately.

Four measures were used: criterion coverage, duplicate rate, similarity to the reference
scenarios, and response time. The duplicate rate is included because a model can raise its
coverage simply by repeating itself.

During a comparison the fallback to the general model is switched off. In normal use the
system answers with the general model when the fine-tuned one fails, so the user still
receives scenarios. In a comparison that same behaviour would fill the fine-tuned column with
the other model's output, and two columns would describe one model. A model that fails
therefore returns nothing, and the empty result is reported instead of being filled in.

Comparisons can be run from the application, for a single ticket or for several at once, and
the results are stored under a run name. Runs made at different times can be reported on their
own or combined into one report, so evidence accumulates instead of being collected once.

**The fine-tuned model works.** It returns valid Gherkin in the JSON structure the
application expects, and it produced a scenario for every acceptance criterion in the on
domain set, which is the same coverage the general model reached. The training moved the
model in the intended direction: it follows the required output format on its own, without
the long instructions the general model needs in its prompt.

Two weaknesses remain, and they have different causes.

The first is speed. The fine-tuned model answers much more slowly, because the hardware
available for this project cannot hold it in graphics memory. The development machine has a
two gigabyte graphics card and the deployment server has none, so the model runs on the
processor instead. A server with a suitable GPU would remove this difference. It was not used
because the project has no budget for GPU hosting.

The second weakness is repetition. The fine-tuned model writes the same scenario more often
than the general model does. Adding more training data of the same kind did not change this,
which points to the type of the training data rather than its amount. The most useful next
step is therefore collecting criteria and scenarios from real product use.

For now the general model continues to serve the deployed system, and the fine-tuned model is
kept for research.
