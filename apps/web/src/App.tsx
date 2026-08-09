import {
  Database,
  FileUp,
  KeyRound,
  LogIn,
  LogOut,
  RefreshCw,
  Search,
  UploadCloud
} from "lucide-react";
import { ChangeEvent, useEffect, useState } from "react";

type DocumentSummary = {
  id: string;
  title: string;
  source_type: string;
  current_version_id: string | null;
  active_chunk_count: number;
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
};

export function App() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [query, setQuery] = useState("OpenSQL pgvector");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [uploading, setUploading] = useState(false);
  const [searching, setSearching] = useState(false);
  const [scope, setScope] = useState<AppliedScope | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [viewer, setViewer] = useState<Viewer | null>(null);

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
    await fetch(path, { method: "POST", body: form });
    event.target.value = "";
    await refreshDocuments();
    setUploading(false);
  }

  async function search() {
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
    setResults(payload.results ?? []);
    setScope(payload.scope ?? null);
    setSearching(false);
  }

  useEffect(() => {
    void refreshViewer();
    void refreshDocuments();
  }, []);

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">
          <Database size={22} />
          <span>OpenSQL AutoRAG</span>
        </div>
        <label className="uploadButton">
          <UploadCloud size={16} />
          <span>{uploading ? "Uploading" : "Upload"}</span>
          <input type="file" accept=".pdf,.docx,.md,.txt" onChange={(event) => upload(event)} />
        </label>
        <button onClick={refreshDocuments}>
          <RefreshCw size={16} />
          Refresh
        </button>

        <div className="identity">
          {viewer?.outline_user ? (
            <>
              <span className="signedIn">
                <KeyRound size={14} />
                {viewer.outline_user_name || viewer.outline_user}
              </span>
              <button onClick={signOut}>
                <LogOut size={16} />
                Sign out
              </button>
              <small>Wiki results are limited to the collections you can read in Outline.</small>
            </>
          ) : (
            <>
              {viewer?.login_available ? (
                <a className="signInButton" href="/api/auth/outline/login?next=/">
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
        <header className="topbar">
          <div>
            <h1>Document AI Search</h1>
            <p>OpenSQL pgvector · delta sync · MCP-ready retrieval</p>
          </div>
          <div className="stat">
            <strong>{documents.length}</strong>
            <span>documents</span>
          </div>
        </header>

        {notice ? <p className="notice">{notice}</p> : null}
        {scope ? (
          <p className="scope">
            {scope.outline_user
              ? `Searched as Outline user ${scope.outline_user} · ${scope.collection_count} readable collection(s)`
              : "Searched without an Outline identity · uploaded documents only"}
          </p>
        ) : null}

        <div className="grid">
          <section className="panel documentPanel">
            <h2>
              <FileUp size={18} />
              Documents
            </h2>
            <div className="tableHeader">
              <span>Title</span>
              <span>Type</span>
              <span>Chunks</span>
              <span>Version</span>
            </div>
            {documents.length === 0 ? <p className="muted">No documents indexed.</p> : null}
            {documents.map((doc) => (
              <article className="tableRow" key={doc.id}>
                <strong>
                  {doc.title}
                  {doc.retired_at ? <em className="retired">removed at source</em> : null}
                </strong>
                <span>{doc.source_type}</span>
                <span>{doc.active_chunk_count}</span>
                <label className="versionButton">
                  New
                  <input
                    type="file"
                    accept=".pdf,.docx,.md,.txt"
                    onChange={(event) => upload(event, doc.id)}
                  />
                </label>
              </article>
            ))}
          </section>

          <section className="panel searchPanel">
            <h2>
              <Search size={18} />
              Search
            </h2>
            <div className="searchbar">
              <input value={query} onChange={(event) => setQuery(event.target.value)} />
              <button onClick={search}>{searching ? "Searching" : "Search"}</button>
            </div>
            <div className="results">
              {results.length === 0 ? <p className="muted">No results loaded.</p> : null}
              {results.map((result) => (
                <article className="result" key={result.chunk_id}>
                  <div className="resultMeta">
                    <strong>{Number(result.score).toFixed(3)}</strong>
                    <span>{result.heading_path || "Document"}</span>
                  </div>
                  <p>{result.text}</p>
                  <span className="source">
                    {result.source_url ? (
                      <a href={result.source_url} target="_blank" rel="noreferrer">
                        {result.document_title}
                        {result.source_system ? ` (${result.source_system})` : null}
                      </a>
                    ) : (
                      result.document_title
                    )}
                    {" · "}p.{result.page_start ?? "-"} · v.{result.version_id.slice(0, 8)}
                  </span>
                </article>
              ))}
            </div>
          </section>
        </div>
      </section>
    </main>
  );
}
