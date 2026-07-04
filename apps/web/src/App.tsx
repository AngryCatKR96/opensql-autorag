import { Database, FileUp, RefreshCw, Search, UploadCloud } from "lucide-react";
import { ChangeEvent, useEffect, useState } from "react";

type DocumentSummary = {
  id: string;
  title: string;
  source_type: string;
  current_version_id: string | null;
  active_chunk_count: number;
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
};

export function App() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [query, setQuery] = useState("OpenSQL pgvector");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [uploading, setUploading] = useState(false);
  const [searching, setSearching] = useState(false);

  async function refreshDocuments() {
    const response = await fetch("/api/documents");
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
    const payload = await response.json();
    setResults(payload.results ?? []);
    setSearching(false);
  }

  useEffect(() => {
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
                <strong>{doc.title}</strong>
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
                    p.{result.page_start ?? "-"} · v.{result.version_id.slice(0, 8)}
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
