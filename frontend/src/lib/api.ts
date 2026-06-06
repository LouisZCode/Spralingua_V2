// Single source of truth for the backend origin the browser talks to.
//
// NEXT_PUBLIC_* is inlined at BUILD time, so this must reference
// process.env.NEXT_PUBLIC_API_URL literally. Set it per deployment (e.g.
// https://your-backend.up.railway.app); the default keeps local dev working
// with no .env.local entry.
const RAW_API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8765";

// Trailing slash trimmed so `${HTTP_BASE}/auth/google` can't double up.
export const HTTP_BASE = RAW_API_URL.replace(/\/+$/, "");

// WebSocket origin derived from the HTTP one: http->ws, https->wss. Deriving it
// (rather than a second env var) means the schemes can never drift — an https
// API always yields a wss socket, which is what browsers require on an https page.
export const WS_BASE = HTTP_BASE.replace(/^http/, "ws");
