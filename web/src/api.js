function resolveBackendUrl() {
  const configured = import.meta.env.VITE_BACKEND_URL?.trim();
  if (configured) return configured.replace(/\/$/, "");
  if (typeof window !== "undefined" && window.location?.origin) {
    return window.location.origin;
  }
  return "http://localhost:7860";
}

export const BACKEND_URL = resolveBackendUrl();

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`${BACKEND_URL}${path}`, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

export const api = {
  health: () => request("GET", "/health"),
  listDecks: () => request("GET", "/api/decks"),
  getDeck: (id) => request("GET", `/api/decks/${id}`),
  status: (id) => request("GET", `/api/decks/${id}/status`),
  patchSlide: (id, n, data) => request("PATCH", `/api/decks/${id}/slides/${n}`, data),
  retryDeck: (id) => request("POST", `/api/decks/${id}/retry`),
  deleteDeck: (id) => request("DELETE", `/api/decks/${id}`),
  imageUrl: (id, n) => `${BACKEND_URL}/api/decks/${id}/slides/${n}/image`,

  // XHR instead of fetch: byte-level upload progress.
  upload: (file, { onProgress } = {}) =>
    new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BACKEND_URL}/api/decks`);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(JSON.parse(xhr.responseText));
        } else {
          let detail = `Upload failed (${xhr.status})`;
          try {
            detail = JSON.parse(xhr.responseText).detail || detail;
          } catch {}
          reject(new Error(detail));
        }
      };
      xhr.onerror = () => reject(new Error("Network error during upload"));
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    }),
};

export function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
