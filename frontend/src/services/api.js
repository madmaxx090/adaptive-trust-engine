const BASE_URL = "http://localhost:8000";

async function parseResponse(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Request failed");
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

export async function scoreSession(sessionData) {
  return parseResponse(await fetch(`${BASE_URL}/session/score`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(sessionData),
  }));
}
