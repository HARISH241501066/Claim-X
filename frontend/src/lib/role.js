import { useState } from 'react'

const KEY = 'claimshield.role'
export const ROLES = [
  { value: 'siu', label: 'SIU team' },
  { value: 'manager', label: 'Manager' },
]

function read() {
  try {
    const value = window.localStorage.getItem(KEY)
    return ROLES.some((r) => r.value === value) ? value : 'siu'
  } catch {
    return 'siu' // storage can be blocked; the default role is used
  }
}

/** Whose notifications to show. There is no login: this only picks which inbox the bell reads. */
export function useRole() {
  const [role, setRoleState] = useState(read)
  const setRole = (value) => {
    setRoleState(value)
    try {
      window.localStorage.setItem(KEY, value)
    } catch {
      /* not remembered */
    }
  }
  return [role, setRole]
}
