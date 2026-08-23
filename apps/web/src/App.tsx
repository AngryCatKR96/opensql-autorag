import {
  Database,
  FilePlus2,
  Files,
  KeyRound,
  ListOrdered,
  LogIn,
  LogOut,
  RefreshCw,
  Search,
  UploadCloud
} from "lucide-react";
import { ChangeEvent, FormEvent, useEffect, useState } from "react";

type DocumentSummary = {
  id: string;
  title: string;
  source_type: string;
  current_version_id: string | null;
  active_chunk_count: number;
  // What the last indexing run did. Null before a document has ever been
  // indexed; reuse of zero on every run means delta sync is not working.
  last_reused_count: number | null;
  last_embedded_count: number | null;
  retired_at: string | null;
};

/** Who the API resolved this caller to be, and how much it let them reach. */
type AppliedScope = {
  outline_user: string | null;
  collection_count: number;
};

/** Whether signing in with Outline is configured, and who is signed in. */
type Viewer = {
  login_available: boolean;
  outline_user: string | null;
  outline_user_name: string | null;
};

type SearchResult = {
  chunk_id: string;
  document_id: string;
  version_id: string;
  text: string;
  score: number;
  heading_path: string;
  page_start: number | null;
  page_end: number | null;
  document_title: string;
  source_system: string | null;
  source_url: string | null;
  // Which retrieval arms returned this chunk, and what each one scored. Empty
  // and null outside hybrid mode, where there is only one arm to report.
  matched_by: string[];
  vector_score: number | null;
  keyword_score: number | null;
};

const ACCEPTED = ".pdf,.docx,.md,.txt";

/** Both arms, in the order they read in the rail beside every result. */
const ARMS = ["vector", "keyword"] as const;

export function App() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [query, setQuery] = useState("OpenSQL pgvector");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [uploading, setUploading] = useState(false);
  const [searching, setSearching] = useState(false);
  const [scope, setScope] = useState<AppliedScope | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [viewer, setViewer] = useState<Viewer | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  /** Open or re-collapse one long passage, leaving the others as they were. */
  function toggle(chunkId: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (!next.delete(chunkId)) next.add(chunkId);
      return next;
    });
  }

  /** Turn the API's refusals into something the console can say out loud. */
  async function explain(response: Response): Promise<string> {
    if (response.status === 401) return "Outline rejected this session. Sign in again.";
    if (response.status === 503) return "Outline is unreachable, so access is unknown.";
    const body = await response.text();
    return `Request failed (${response.status}). ${body}`.trim();
  }

  async function refreshViewer() {
    const response = await fetch("/api/auth/outline/me");
    setViewer(response.ok ? await response.json() : null);
  }

  async function signOut() {
    await fetch("/api/auth/outline/logout", { method: "POST" });
    setScope(null);
    setResults([]);
    await Promise.all([refreshViewer(), refreshDocuments()]);
  }

  async function refreshDocuments() {
    const response = await fetch("/api/documents");
    if (!response.ok) {
      setDocuments([]);
      setNotice(await explain(response));
      return;
    }
    setNotice(null);
    setDocuments(await response.json());
  }

  async function upload(event: ChangeEvent<HTMLInputElement>, documentId?: string) {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploading(true);
    const form = new FormData();
    form.append("file", file);
    const path = documentId ? `/api/documents/${documentId}/versions` : "/api/documents";
    const response = await fetch(path, { method: "POST", body: form });
    event.target.value = "";
    // A rejected upload used to look exactly like a successful one, which left
    // the file silently missing from a list that had refreshed without it.
    if (!response.ok) {
      setNotice(await explain(response));
      setUploading(false);
      return;
    }
    await refreshDocuments();
    setUploading(false);
  }

  async function search(event?: FormEvent) {
    event?.preventDefault();
    setSearching(true);
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ query, top_k: 5 })
    });
    if (!response.ok) {
      setResults([]);
      setScope(null);
      setNotice(await explain(response));
      setSearching(false);
      return;
    }
    const payload = await response.json();
    setNotice(null);
    setExpanded(new Set());
    setResults(payload.results ?? []);
    setScope(payload.scope ?? null);
    setSearching(false);
  }

  useEffect(() => {
    void refreshViewer();
    void refreshDocuments();
  }, []);

  // A reciprocal-rank score is only meaningful next to the other scores in its
  // own result set, so the bars are drawn against the strongest hit.
  const topScore = Math.max(...results.map((result) => Number(result.score)), 0);

  return (
    <main className="shell">
      <aside className="rail">
        <div className="brand">
          <Database size={19} />
          <span>OpenSQL AutoRAG</span>
        </div>

        <p className="railLabel">Corpus</p>
        <label className="btn btn-primary">
          <UploadCloud size={16} />
          <span>{uploading ? "Uploading…" : "Upload a document"}</span>
          <input
            type="file"
            accept={ACCEPTED}
            disabled={uploading}
            onChange={(event) => upload(event)}
          />
        </label>
        <button className="btn btn-ghost" onClick={refreshDocuments}>
          <RefreshCw size={16} />
          Refresh
        </button>

        <div className="identity">
          {viewer?.outline_user ? (
            <>
              <p className="signedIn">
                <KeyRound size={14} />
                <span>{viewer.outline_user_name || viewer.outline_user}</span>
              </p>
              <button className="btn btn-ghost" onClick={signOut}>
                <LogOut size={16} />
                Sign out
              </button>
              <small>Wiki results are limited to the collections you can read in Outline.</small>
            </>
          ) : (
            <>
              {viewer?.login_available ? (
                <a className="btn btn-ghost" href="/api/auth/outline/login?next=/">
                  <LogIn size={16} />
                  Sign in with Outline
                </a>
              ) : null}
              <small>
                {viewer?.login_available
                  ? "Only documents uploaded here are searched until you sign in."
                  : "Outline sign-in is not configured, so only documents uploaded here are searched."}
              </small>
            </>
          )}
        </div>
      </aside>

      <section className="workspace">
        <header className="masthead">
          <h1>Document AI Search</h1>
          <p className="eyebrow">OpenSQL pgvector · delta sync · MCP-ready retrieval</p>
        </header>

        <form className="searchbar" onSubmit={search}>
          <Search size={18} />
          <input
            value={query}
            aria-label="Search the corpus"
            placeholder="Search the corpus"
            onChange={(event) => setQuery(event.target.value)}
          />
          <button className="btn btn-primary" type="submit" disabled={searching}>
            {searching ? "Searching…" : "Search"}
          </button>
        </form>

        {notice ? <p className="notice">{notice}</p> : null}
        {scope ? (
          <p className="scope">
            {scope.outline_user
              ? `Searched as Outline user ${scope.outline_user} · ${scope.collection_count} readable collection(s)`
              : "Searched without an Outline identity · uploaded documents only"}
          </p>
        ) : null}

        <div className="grid">
          <section className="panel">
            <div className="panelHead">
              <ListOrdered size={17} />
              <h2>Results</h2>
              {results.length > 0 ? <span className="count">{results.length}</span> : null}
            </div>
            {results.length === 0 ? (
              <p className="empty">
                Ask a question. Both arms run — one matches what a passage means, the other
                matches the words it contains.
              </p>
            ) : null}
            {results.map((result, index) => {
              const matched = result.matched_by ?? [];
              const long = result.text.length > 420;
              const open = expanded.has(result.chunk_id);
              return (
                <article
                  className="result"
                  key={result.chunk_id}
                  style={{ animationDelay: `${Math.min(index, 8) * 40}ms` }}
                >
                  <div className="provenance">
                    <span className="score">{Number(result.score).toFixed(3)}</span>
                    <div className="scoreBar">
                      <i
                        style={{
                          width: topScore > 0 ? `${(Number(result.score) / topScore) * 100}%` : "0%"
                        }}
                      />
                    </div>
                    {/* Which arm found it. A chunk both arms agree on means the
                        wording and the meaning both matched, which is the case
                        hybrid retrieval exists to promote. Outside hybrid mode
                        there is only one arm, so there is nothing to compare. */}
                    {matched.length > 0 ? (
                      <div className="arms">
                        {ARMS.map((arm) => {
                          const fired = matched.includes(arm);
                          const scored = arm === "vector" ? result.vector_score : result.keyword_score;
                          return (
                            <span
                              className={`tick ${fired ? `tick-${arm}` : "tick-off"}`}
                              key={arm}
                              title={
                                fired
                                  ? arm === "vector"
                                    ? `Matched on meaning · similarity ${Number(scored).toFixed(3)}`
                                    : `Matched on wording · keyword rank ${Number(scored).toFixed(4)}`
                                  : `The ${arm} arm did not return this passage`
                              }
                            >
                              {arm === "vector" ? "vec" : "key"}
                            </span>
                          );
                        })}
                      </div>
                    ) : null}
                  </div>

                  <div>
                    <p className="crumb">{result.heading_path || "Document"}</p>
                    <p className={`passage${long && !open ? " passage-clamped" : ""}`}>
                      {result.text}
                    </p>
                    {long ? (
                      <button className="moreBtn" onClick={() => toggle(result.chunk_id)}>
                        {open ? "Show less" : "Show the full passage"}
                      </button>
                    ) : null}
                    <span className="source">
                      {result.source_url ? (
                        <a href={result.source_url} target="_blank" rel="noreferrer">
                          {result.document_title}
                          {result.source_system ? ` (${result.source_system})` : null}
                        </a>
                      ) : (
                        result.document_title
                      )}
                      {/* Markdown and text have no pages, and "p.-" is noise. */}
                      {result.page_start !== null ? ` · p.${result.page_start}` : null}
                      {` · v.${result.version_id.slice(0, 8)}`}
                    </span>
                  </div>
                </article>
              );
            })}
          </section>

          <section className="panel">
            <div className="panelHead">
              <Files size={17} />
              <h2>Documents</h2>
              {documents.length > 0 ? <span className="count">{documents.length}</span> : null}
            </div>
            {documents.length === 0 ? (
              <p className="empty">
                Nothing indexed yet. Upload a PDF, Word, Markdown or text file to start.
              </p>
            ) : null}
            {documents.map((doc) => (
              <article className="doc" key={doc.id}>
                <div>
                  <strong>
                    {doc.title}
                    {doc.retired_at ? <em className="retired">removed at source</em> : null}
                  </strong>
                  <p className="docMeta">
                    {doc.source_type} · {doc.active_chunk_count} chunks
                    {doc.current_version_id ? ` · v.${doc.current_version_id.slice(0, 8)}` : null}
                  </p>
                  {/* The last run's split between kept and recomputed vectors.
                      Delta sync is a claim until this number is visible where
                      somebody looks after editing a page. */}
                  {doc.last_embedded_count !== null ? (
                    <p className="docSync">
                      <span className="reused">{doc.last_reused_count} reused</span>
                      <span className="embedded">{doc.last_embedded_count} embedded</span>
                    </p>
                  ) : null}
                </div>
                <label className="btn iconBtn" title={`Upload a new version of ${doc.title}`}>
                  <FilePlus2 size={15} />
                  <span className="srOnly">Upload a new version of {doc.title}</span>
                  <input
                    type="file"
                    accept={ACCEPTED}
                    disabled={uploading}
                    onChange={(event) => upload(event, doc.id)}
                  />
                </label>
              </article>
            ))}
          </section>
        </div>
      </section>
    </main>
  );
}
