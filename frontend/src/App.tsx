import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Layout from './components/Layout';
import ProtectedRoute from './components/ProtectedRoute';
import Login from './pages/Login';
import Signup from './pages/Signup';
import Dashboard from './pages/Dashboard';
import StockDetail from './pages/StockDetail';
import Portfolio from './pages/Portfolio';
import Predictions from './pages/Predictions';
import AIEngineLayout from './pages/AIEngineLayout';
import AIEngine from './pages/AIEngine';
import AIAgent from './pages/AIAgent';
import Backtest from './pages/Backtest';
import PaperTrading from './pages/PaperTrading';
import PatternMemory from './pages/PatternMemory';
import LiveSessions from './pages/LiveSessions';
import ModelRegistry from './pages/ModelRegistry';
import Orders from './pages/Orders';
import './styles/globals.css';

const App: React.FC = () => {
  return (
    <Router basename="/neuradex">
      <Routes>
        {/* Public routes — no Layout */}
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />

        {/* Protected routes — inside Layout */}
        <Route
          path="/*"
          element={
            <ProtectedRoute>
              <Layout>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/stocks/:symbol" element={<StockDetail />} />
                  <Route path="/portfolio" element={<Portfolio />} />
                  <Route path="/predictions" element={<Predictions />} />

                  {/* AI Engine — nested sub-routes under shared layout */}
                  <Route path="/ai-engine" element={<AIEngineLayout />}>
                    <Route index element={<AIEngine />} />
                    <Route path="agents" element={<AIAgent />} />
                    <Route path="backtest" element={<Backtest />} />
                    <Route path="paper-trading" element={<PaperTrading />} />
                    <Route path="sessions" element={<LiveSessions />} />
                    <Route path="memory" element={<PatternMemory />} />
                  </Route>

                  {/* Redirect old standalone routes → new locations */}
                  <Route path="/agent" element={<Navigate to="/ai-engine/agents" replace />} />
                  <Route path="/backtest" element={<Navigate to="/ai-engine/backtest" replace />} />
                  <Route path="/paper-trading" element={<Navigate to="/ai-engine/paper-trading" replace />} />
                  <Route path="/risk" element={<Navigate to="/portfolio" replace />} />

                  <Route path="/models" element={<ModelRegistry />} />
                  <Route path="/orders" element={<Orders />} />
                </Routes>
              </Layout>
            </ProtectedRoute>
          }
        />
      </Routes>
    </Router>
  );
};

export default App;
