import axios from 'axios'

export const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

export const http = axios.create({ baseURL: API_URL, timeout: 60000 })

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
