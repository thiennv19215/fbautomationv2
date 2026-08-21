export async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `Yêu cầu thất bại (${response.status})`);
  return data;
}

export const endpoints = {
  extensions: () => api("/api/extensions"),
  accounts: () => api("/api/accounts"),
  scripts: () => api("/api/scripts"),
  jobs: () => api("/api/jobs"),
  health: () => api("/api/health"),
  createAccount: (body) => api("/api/accounts", { method: "POST", body: JSON.stringify(body) }),
  updateAccount: (id, body) => api(`/api/accounts/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteAccount: (id) => api(`/api/accounts/${id}`, { method: "DELETE" }),
  createScript: (body) => api("/api/scripts", { method: "POST", body: JSON.stringify(body) }),
  updateScript: (id, body) => api(`/api/scripts/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteScript: (id) => api(`/api/scripts/${id}`, { method: "DELETE" }),
  createJob: (body) => api("/api/jobs", { method: "POST", body: JSON.stringify(body) }),
  createBulkJobs: (body) => api("/api/jobs/bulk", { method: "POST", body: JSON.stringify(body) }),
  cancelJob: (id) => api(`/api/jobs/${id}/cancel`, { method: "POST" }),
  retryJob: (id) => api(`/api/jobs/${id}/retry`, { method: "POST" }),
  templateStatus: (id) => api(`/api/extensions/${encodeURIComponent(id)}/template-status`),
  extensionIdentity: (id) => api(`/api/extensions/${encodeURIComponent(id)}/identity`),
  media: () => api("/api/media"),
};
