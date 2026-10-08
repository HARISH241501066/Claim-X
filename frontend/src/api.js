import axios from 'axios'

export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export const http = axios.create({ baseURL: API_URL, timeout: 60000 })

// The sign-in token: kept in memory and in sessionStorage (so a reload keeps you signed in, and
// closing the tab signs you out). The API checks it on every request; the screens only reflect that.
const TOKEN_KEY = 'claimshield.token'
let token = null
let onUnauthorized = () => {}
try {
  token = window.sessionStorage.getItem(TOKEN_KEY)
} catch {
  token = null // storage can be blocked; the token then lives in memory only
}

export const getToken = () => token
export function setToken(value) {
  token = value
  try {
    if (value) window.sessionStorage.setItem(TOKEN_KEY, value)
    else window.sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    /* memory only */
  }
}
/** Called when the API says the token is no longer valid (expired, or the account was switched off). */
export const setUnauthorizedHandler = (fn) => {
  onUnauthorized = fn
}

http.interceptors.request.use((config) => {
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})
http.interceptors.response.use(
  (response) => response,
  (error) => {
    const url = error?.config?.url ?? ''
    if (error?.response?.status === 401 && token && !url.includes('/auth/login')) onUnauthorized()
    return Promise.reject(error)
  },
)

const get = (url, params, signal) => http.get(url, { params, signal }).then((r) => r.data)
const post = (url, body) => http.post(url, body).then((r) => r.data)

export const getHealth = (signal) => get('/health', undefined, signal)
export const getOverview = (signal) => get('/overview', undefined, signal)
export const getQueue = (params, signal) => get('/queue', params, signal)
export const getCase = (id, signal) => get(`/cases/${id}`, undefined, signal)
export const getGraph = (id, signal) => get(`/cases/${id}/graph`, undefined, signal)
export const getBrief = (id, horizon, signal) => get(`/cases/${id}/brief`, { horizon }, signal)
export const getEvidence = (id, key, signal) => get(`/cases/${id}/evidence/${key}`, undefined, signal)
export const postDecision = (id, body) => post(`/cases/${id}/decision`, body)
export const postOverride = (id, body) => post(`/cases/${id}/priority-override`, body)

/** A short, human-readable message for any failed request. */
export function errorMessage(err) {
  if (err?.response) {
    const { status, data } = err.response
    const detail = data?.detail
    if (status === 503) return 'The analysis is still starting up. Try again in a few seconds.'
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0]
      const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : null
      return field ? `${field}: ${first.msg}` : first.msg
    }
    return `The server answered with an error (${status}).`
  }
  if (err?.code === 'ECONNABORTED') return 'The server took too long to answer.'
  return `Cannot reach the API at ${API_URL}. Is it running?`
}

const put = (url, body) => http.put(url, body).then((r) => r.data)

export const getNotifications = (params, signal) => get('/notifications', params, signal)
export const postNotificationRead = (id) => post(`/notifications/${id}/read`)
export const postReadAll = () => post('/notifications/read-all')
export const postTestEmail = () => post('/admin/test-email')
export const getOutbound = (id, signal) => get(`/cases/${id}/outbound`, undefined, signal)
export const postOutbound = (id, body) => post(`/cases/${id}/outbound`, body)
export const putOutbound = (id, body) => put(`/outbound/${id}`, body)
export const postApprove = (id, body) => post(`/outbound/${id}/approve`, body)

export const login = (username, password) => post('/auth/login', { username, password })
export const getMe = (signal) => get('/auth/me', undefined, signal)
export const getMembers = (unitId, signal) => get(`/units/${unitId}/members`, undefined, signal)
export const getWorkload = (unitId, signal) => get(`/units/${unitId}/workload`, undefined, signal)
export const postAssign = (caseId, body) => post(`/cases/${caseId}/assign`, body)
export const postUnassign = (caseId, body) => post(`/cases/${caseId}/unassign`, body)

/** Fetch a report with the token and hand it to the browser as a file (a plain link cannot send it). */
export async function downloadFile(url, fallbackName) {
  const response = await http.get(url, { responseType: 'blob' })
  const match = /filename="?([^";]+)"?/.exec(response.headers['content-disposition'] ?? '')
  const link = document.createElement('a')
  link.href = URL.createObjectURL(response.data)
  link.download = match?.[1] ?? fallbackName
  document.body.appendChild(link)
  link.click()
  link.remove()
  setTimeout(() => URL.revokeObjectURL(link.href), 1000)
  return link.download
}
export const downloadCaseReport = (id) => downloadFile(`/cases/${id}/report.pdf`, `${id}-report.pdf`)
export const downloadUnitReport = (unitId, format) =>
  downloadFile(`/units/${unitId}/report.${format}`, `unit-report.${format}`)

export const postRerun = () => post('/admin/rerun')
export const postPrewarm = (top) => http.post('/admin/prewarm-briefs', null, { params: { top } }).then((r) => r.data)
export const getUsers = (signal) => get('/admin/users', undefined, signal)
export const getUnits = (signal) => get('/admin/units', undefined, signal)
export const getUnrouted = (signal) => get('/admin/unrouted', undefined, signal)
export const postUser = (body) => post('/admin/users', body)
export const postUserActive = (id, active) => post(`/admin/users/${id}/active`, { active })
export const putUnitRegion = (id, region) => put(`/admin/units/${id}`, { region })
