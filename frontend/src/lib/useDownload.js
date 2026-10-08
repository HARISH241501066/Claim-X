import { useState } from 'react'
import { errorMessage } from '../api'

/** Runs a download and keeps its message. The API decides who may download; a refusal is shown here as its message. */
export function useDownload() {
  const [state, setState] = useState({ busy: null, message: null, ok: false })
  async function run(key, work) {
    setState({ busy: key, message: null, ok: false })
    try {
      const name = await work()
      setState({ busy: null, message: `Downloaded ${name}.`, ok: true })
    } catch (err) {
      const status = err?.response?.status
      // a blob error body is not JSON text, so name the common refusals directly
      const message = status === 403 ? 'You are not allowed to download this report.' : errorMessage(err)
      setState({ busy: null, message, ok: false })
    }
  }
  return [state, run]
}
