import React from 'react';
import SessionManager from '../components/SessionManager';

// Multi-session live paper trading — run several stocks at once, all server-side.
const PaperTrading: React.FC = () => <SessionManager mode="paper" />;

export default PaperTrading;
