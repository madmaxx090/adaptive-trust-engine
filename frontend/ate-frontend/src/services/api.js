const BASE_URL = "http://localhost:8008";

// FastAPI sends 422 validation failures as an array of Pydantic error objects;
// throwing that directly stringifies to "[object Object]" for every caller.
function describeError(detail) {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        const field = Array.isArray(item?.loc) ? item.loc.slice(1).join(".") : "";
        const message = item?.msg || "Invalid request";
        return field ? `${field}: ${message}` : message;
      })
      .join("; ");
  }
  return "Request failed";
}

async function parseResponse(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(describeError(data.detail));
  return data;
}

export async function getHealth() {
  return parseResponse(await fetch(`${BASE_URL}/health`));
}

export async function getSessions() {
  return parseResponse(await fetch(`${BASE_URL}/sessions`));
}

export async function getSessionDetail(sessionId) {
  return parseResponse(await fetch(`${BASE_URL}/sessions/${sessionId}`));
}

export async function getAuditLog({ page = 1, limit = 50, eventType } = {}) {
  const params = new URLSearchParams({ page: String(page), limit: String(limit) });
  if (eventType) params.set("event_type", eventType);
  return parseResponse(await fetch(`${BASE_URL}/audit-log?${params.toString()}`));
}

export async function scoreSession(sessionData) {
  return parseResponse(await fetch(`${BASE_URL}/session/score`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sessionData),
  }));
}
