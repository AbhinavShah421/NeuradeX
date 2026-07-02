// Narrow an unknown catch-block error down to a human-readable string.
// Axios errors carry the backend's message at err.response.data.detail; plain
// Errors carry it on err.message. Using `unknown` here (instead of `any`) forces
// every call site to go through this narrowing instead of blindly poking at
// properties that may not exist.
export function getErrorMessage(e: unknown, fallback = 'Unexpected error'): string {
  if (e && typeof e === 'object') {
    const withResponse = e as { response?: { data?: { detail?: string } }; message?: string };
    return withResponse.response?.data?.detail ?? withResponse.message ?? fallback;
  }
  if (typeof e === 'string') return e;
  return fallback;
}
