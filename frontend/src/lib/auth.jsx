import { useCallback, useEffect, useMemo, useState } from 'react'
import * as api from '../api'
import { AuthContext } from './authContext'

/** The signed-in user, the token and the sign-in / sign-out actions. */
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null)
  // "checking" until a stored token has been confirmed with the server, so a reload never flashes the login page
  const [checking, setChecking] = useState(() => Boolean(api.getToken()))
  const [notice, setNotice] = useState(null)

  const signOut = useCallback((message = null) => {
    api.setToken(null)
    setUser(null)
    setNotice(message)
  }, [])

  useEffect(() => {
    api.setUnauthorizedHandler(() => signOut('Your session has ended. Sign in again.'))
    return () => api.setUnauthorizedHandler(() => {})
  }, [signOut])

  useEffect(() => {
    if (!api.getToken()) return undefined
    let live = true
    api.getMe().then(
      (me) => {
        if (live) {
          setUser(me)
          setChecking(false)
        }
      },
      () => {
        if (live) {
          api.setToken(null)
          setChecking(false)
        }
      },
    )
    return () => {
      live = false
    }
  }, [])

  const signIn = useCallback(async (username, password) => {
    const result = await api.login(username, password)
    api.setToken(result.access_token)
    setNotice(null)
    setUser(result.user)
    return result.user
  }, [])

  const value = useMemo(() => ({ user, checking, notice, signIn, signOut }), [user, checking, notice, signIn, signOut])
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
