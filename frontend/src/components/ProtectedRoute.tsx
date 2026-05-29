import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '../stores/authStore';

const ProtectedRoute: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, expiresAt } = useAuthStore();
  const location = useLocation();

  const expired = expiresAt ? new Date(expiresAt) < new Date() : false;

  if (!isAuthenticated || expired) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
};

export default ProtectedRoute;
