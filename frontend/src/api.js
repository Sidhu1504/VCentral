export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

function buildUrl(path, params) {
  const url = new URL(path, window.location.origin)
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '' && v !== false) url.searchParams.set(k, v)
  })
  return url.toString()
}

async function request(method, path, body, params) {
  const headers = { 'X-Requested-With': 'vcentral' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(buildUrl(path, params), {
    method,
    credentials: 'same-origin',
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    window.dispatchEvent(new Event('vcentral:unauthorized'))
  }
  if (!res.ok) {
    let message = res.statusText
    try {
      const data = await res.json()
      if (typeof data.detail === 'string') message = data.detail
      else if (Array.isArray(data.detail) && data.detail[0]?.msg) message = data.detail[0].msg
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, message)
  }
  if (res.status === 204) return null
  return (res.headers.get('content-type') || '').includes('application/json') ? res.json() : res.text()
}

export const api = {
  get: (path, params) => request('GET', path, undefined, params),
  post: (path, body = {}) => request('POST', path, body),
  patch: (path, body) => request('PATCH', path, body),
  put: (path, body) => request('PUT', path, body),
  del: (path) => request('DELETE', path),
  url: buildUrl,
}
