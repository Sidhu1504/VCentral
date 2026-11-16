import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api } from './api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [user, setUser] = useState(undefined) // undefined = loading, null = signed out

  const refresh = useCallback(async () => {
    try {
      setUser(await api.get('/api/auth/me'))
    } catch {
      setUser(null)
    }
  }, [])

  useEffect(() => {
    refresh()
    const onUnauthorized = () => setUser(null)
    window.addEventListener('vcentral:unauthorized', onUnauthorized)
    return () => window.removeEventListener('vcentral:unauthorized', onUnauthorized)
  }, [refresh])

  const login = async (username, password) => setUser(await api.post('/api/auth/login', { username, password }))
  const logout = async () => {
    await api.post('/api/auth/logout').catch(() => {})
    setUser(null)
  }
  const can = (...roles) => !!user && roles.includes(user.role)

  return <AuthContext.Provider value={{ user, setUser, login, logout, refresh, can }}>{children}</AuthContext.Provider>
}

export const useAuth = () => useContext(AuthContext)
