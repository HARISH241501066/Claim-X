import { useState } from 'react'

const KEY = 'claimshield.reviewer'

function read() {
  try {
    return window.localStorage.getItem(KEY) ?? ''
  } catch {
    return '' // storage can be blocked; the name is then simply not remembered
  }
}

/** The reviewer's name: required for every decision, remembered in this browser only. */
export function useReviewer() {
  const [reviewer, setReviewerState] = useState(read)
  const setReviewer = (name) => {
    setReviewerState(name)
    try {
      window.localStorage.setItem(KEY, name)
    } catch {
      /* not remembered */
    }
  }
  return [reviewer, setReviewer]
}
