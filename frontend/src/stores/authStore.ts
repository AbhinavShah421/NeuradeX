import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { BrokerType } from '../types';

interface UserProfile {
  name: string;
  email: string;
  initials: string;
  accountId: string;
}

interface AuthState {
  token: string | null;
  broker: BrokerType | null;
  expiresAt: string | null;
  isAuthenticated: boolean;
  userId: number | null;
  email: string | null;
  profile: UserProfile | null;
  setAuth: (token: string, broker: BrokerType, expiresAt: string, userId?: number, email?: string) => void;
  setProfile: (profile: UserProfile) => void;
  clearAuth: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      broker: null,
      expiresAt: null,
      isAuthenticated: false,
      userId: null,
      email: null,
      profile: null,

      setAuth: (token, broker, expiresAt, userId, email) =>
        set({ token, broker, expiresAt, isAuthenticated: true, userId: userId ?? null, email: email ?? null }),

      setProfile: (profile) => set({ profile }),

      clearAuth: () =>
        set({ token: null, broker: null, expiresAt: null, isAuthenticated: false, userId: null, email: null, profile: null }),
    }),
    { name: 'neuradex-auth' }
  )
);
